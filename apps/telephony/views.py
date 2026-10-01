from datetime import datetime, timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.viewsets import TenantScopedViewSetMixin

from .adapters import get_telephony_provider
from .models import Call, CallbackTask, IVRRoute
from .serializers import (
    CallbackTaskSerializer,
    CallHistorySerializer,
    CallSerializer,
    ClickToCallSerializer,
    IVRRouteSerializer,
)


def _parse_window(request):
    """?start=YYYY-MM-DD&end=YYYY-MM-DD parsing — same convention as
    apps.analytics.views._parse_window (timezone.make_aware over
    datetime.strptime(..., "%Y-%m-%d")). Not imported directly because that
    helper is private to apps.analytics and defaults to a rolling 7-day
    window, whereas operator productivity is read as a daily snapshot and
    should default to today (see apps.analytics.services._today_range for
    the equivalent "today" convention used elsewhere in the codebase)."""
    end_param = request.query_params.get("end")
    start_param = request.query_params.get("start")

    if not start_param and not end_param:
        start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)

    # end param is a date (YYYY-MM-DD); strptime gives midnight of that day.
    # Add 1 day so the range is half-open [start, end+1d) and includes the
    # full end date.  e.g. end=2026-09-29 → filter < 2026-09-30 00:00:00.
    end = timezone.make_aware(datetime.strptime(end_param, "%Y-%m-%d")) + timedelta(days=1) if end_param else timezone.localtime()
    start = timezone.make_aware(datetime.strptime(start_param, "%Y-%m-%d")) if start_param else end - timedelta(days=1)
    return start, end


class CallViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = CallSerializer
    queryset = Call.objects.all()
    filterset_fields = ["direction", "status", "department", "operator", "patient"]
    search_fields = ["from_number", "to_number"]

    @action(detail=False, methods=["post"], url_path="click-to-call")
    def click_to_call(self, request):
        """
        Initiates an outbound click-to-call via HoduPBX.

        Flow:
        1. Call record created immediately with status=RINGING — API returns 201 right away.
        2. HoduPBX click2call runs in a background thread (rings extension first).
        3. Once extension is answered, HoduPBX bridges to to_number and returns a UUID.
        4. Background thread updates provider_call_id on the Call record.
        """
        import threading
        from django.conf import settings

        serializer = ClickToCallSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        operator = request.user
        provider = get_telephony_provider()

        # Use operator's SIP extension if set, otherwise fall back to configured default
        from_ext = (
            getattr(operator, "phone", "")
            or getattr(settings, "HODUPBX_DEFAULT_EXTENSION", "101")
        )

        # Create the Call record immediately — status RNR (pending), provider_call_id set on answer
        call = Call.objects.create(
            hospital=operator.hospital,
            direction=Call.Direction.OUTBOUND,
            status=Call.Status.RNR,  # temporary — updated to ANSWERED by background thread on pickup
            from_number=from_ext,
            to_number=serializer.validated_data["to_number"],
            patient_id=serializer.validated_data.get("patient"),
            operator=operator,
            started_at=timezone.now(),
            provider_call_id=f"pending-{from_ext}-{serializer.validated_data['to_number']}",
        )

        def _fire_click2call(call_id, from_number, to_number):
            """Background thread: sends click2call to HoduPBX and updates the Call record."""
            try:
                uuid = provider.initiate_call(from_number=from_number, to_number=to_number)
                # Real UUID — HoduPBX accepted the call, extension was answered
                Call.objects.filter(id=call_id).update(
                    provider_call_id=uuid,
                    status=Call.Status.ANSWERED,
                    answered_at=timezone.now(),
                )
                print(f"[click2call] Call {call_id} answered. provider_call_id={uuid}")
            except Exception as exc:
                print(f"[click2call] Call {call_id} failed: {type(exc).__name__}: {exc}")
                Call.objects.filter(id=call_id).update(
                    status=Call.Status.FAILED,
                    provider_call_id=f"failed-{from_number}-{to_number}",
                )

        thread = threading.Thread(
            target=_fire_click2call,
            args=(call.id, from_ext, serializer.validated_data["to_number"]),
            daemon=True,
        )
        thread.start()

        return Response(CallSerializer(call).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="operator-productivity")
    def operator_productivity(self, request):
        """§1 — calls handled, talk time, conversion, per operator.
        Accepts optional ?start=&end= (YYYY-MM-DD), defaulting to today."""
        start, end = _parse_window(request)
        qs = self.filter_queryset(self.get_queryset()).exclude(operator__isnull=True).filter(
            started_at__gte=start, started_at__lt=end
        )
        report = qs.values("operator_id", "operator__email").annotate(
            calls_handled=Count("id"),
            answered=Count("id", filter=Q(status=Call.Status.ANSWERED)),
            missed=Count("id", filter=Q(status=Call.Status.MISSED)),
            avg_duration_seconds=Avg("duration_seconds"),
        ).order_by("-calls_handled")
        return Response(list(report))

    @action(detail=False, methods=["get"], url_path="active-calls")
    def active_calls(self, request):
        """Returns live in-progress calls from the telephony provider (e.g. HoduPBX)."""
        provider = get_telephony_provider()
        if hasattr(provider, "get_active_calls"):
            return Response(provider.get_active_calls())
        return Response([])

    # ------------------------------------------------------------------
    # Call-history helpers
    # ------------------------------------------------------------------

    def _call_history_response(self, request, direction):
        """Shared implementation for incoming-history and outgoing-history.

        Query params
        ------------
        start, end  : YYYY-MM-DD date range (defaults: today)
        status      : answered | missed | rnr | busy | failed | voicemail
        department  : department PK
        operator    : user PK
        patient     : patient PK
        search      : searched against from_number / to_number
        """
        start, end = _parse_window(request)

        qs = (
            self.get_queryset()
            .filter(direction=direction, started_at__gte=start, started_at__lt=end)
            .select_related("operator", "patient", "department")
        )

        # Optional filters
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        dept_filter = request.query_params.get("department")
        if dept_filter:
            qs = qs.filter(department_id=dept_filter)

        operator_filter = request.query_params.get("operator")
        if operator_filter:
            qs = qs.filter(operator_id=operator_filter)

        patient_filter = request.query_params.get("patient")
        if patient_filter:
            qs = qs.filter(patient_id=patient_filter)

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(Q(from_number__icontains=search) | Q(to_number__icontains=search))

        # Summary stats (computed before pagination)
        aggregates = qs.aggregate(
            total=Count("id"),
            answered=Count("id", filter=Q(status=Call.Status.ANSWERED)),
            missed=Count("id", filter=Q(status=Call.Status.MISSED)),
            rnr=Count("id", filter=Q(status=Call.Status.RNR)),
            avg_duration_seconds=Avg("duration_seconds"),
        )
        summary = {
            "direction": direction,
            "from": start.date().isoformat(),
            "to": end.date().isoformat(),
            "total_calls": aggregates["total"],
            "answered": aggregates["answered"],
            "missed": aggregates["missed"],
            "rnr": aggregates["rnr"],
            "avg_duration_seconds": round(aggregates["avg_duration_seconds"] or 0, 1),
        }

        # Paginate
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = CallHistorySerializer(page, many=True)
            paginated = self.get_paginated_response(serializer.data)
            paginated.data["summary"] = summary
            return paginated

        serializer = CallHistorySerializer(qs, many=True)
        return Response({"summary": summary, "results": serializer.data})

    @action(detail=False, methods=["get"], url_path="incoming-history")
    def incoming_history(self, request):
        """Paginated inbound call history with summary stats.

        GET /api/telephony/calls/incoming-history/
        Optional query params: start, end, status, department, operator, patient, search
        """
        return self._call_history_response(request, Call.Direction.INBOUND)

    @action(detail=False, methods=["get"], url_path="outgoing-history")
    def outgoing_history(self, request):
        """Paginated outbound call history with summary stats.

        GET /api/telephony/calls/outgoing-history/
        Optional query params: start, end, status, department, operator, patient, search
        """
        return self._call_history_response(request, Call.Direction.OUTBOUND)


class CallbackTaskViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = CallbackTaskSerializer
    # select_related: CallbackTaskSerializer.ivr_path (source="call.ivr_path")
    queryset = CallbackTask.objects.select_related("call")
    filterset_fields = ["status", "department", "owner"]

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        task = self.get_object()
        task.owner = request.user
        task.status = CallbackTask.Status.IN_PROGRESS
        task.save(update_fields=["owner", "status"])
        return Response(CallbackTaskSerializer(task).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        task = self.get_object()
        task.status = CallbackTask.Status.DONE
        task.resolved_at = timezone.now()
        task.notes = request.data.get("notes", task.notes)
        task.save(update_fields=["status", "resolved_at", "notes"])
        return Response(CallbackTaskSerializer(task).data)

    @action(detail=True, methods=["post"])
    def log_attempt(self, request, pk=None):
        task = self.get_object()
        task.attempt_count = task.attempt_count + 1
        task.save(update_fields=["attempt_count"])
        return Response(CallbackTaskSerializer(task).data)


class IVRRouteViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = IVRRouteSerializer
    queryset = IVRRoute.objects.all()
    filterset_fields = ["department", "language", "is_active"]


class TelephonyWebhookView(APIView):
    """Ingress for the telephony/IVR provider's call events. Unauthenticated
    at the DRF layer by design (providers can't do JWT login) — production
    deployments must front this with the provider's signature/HMAC check in
    the concrete adapter (see apps.telephony.adapters) before trusting the
    payload."""

    permission_classes = [AllowAny]
    serializer_class = CallSerializer

    def post(self, request, hospital_id):
        provider = get_telephony_provider()
        normalized = provider.normalize_webhook_payload(request.data)

        if not normalized.get("started_at"):
            return Response({"detail": "started_at is required."}, status=status.HTTP_400_BAD_REQUEST)

        call, _ = Call.objects.update_or_create(
            hospital_id=hospital_id,
            provider_call_id=normalized["provider_call_id"],
            defaults={
                "direction": normalized["direction"],
                "status": normalized["status"],
                "from_number": normalized["from_number"],
                "to_number": normalized["to_number"],
                "started_at": normalized["started_at"],
                "answered_at": normalized["answered_at"],
                "ended_at": normalized["ended_at"],
                "duration_seconds": normalized["duration_seconds"] or 0,
                "recording_url": normalized["recording_url"],
                "raw_payload": request.data,
            },
        )

        if normalized["status"] in ["missed", "no_answer"]:
            from apps.automation.engine import execute_workflow
            execute_workflow(
                trigger_type="missed_call",
                event_data={
                    "from_number": normalized["from_number"],
                    "call_id": str(call.id),
                    "started_at": str(normalized["started_at"]),
                },
                hospital_id=hospital_id,
            )

        return Response(CallSerializer(call).data, status=status.HTTP_201_CREATED)

