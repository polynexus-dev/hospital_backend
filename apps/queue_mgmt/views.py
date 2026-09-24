from datetime import timedelta

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import TenantCRUDViewSet, model_serializer

from .models import QueueDisplay, QueueToken, ServicePoint


def avg_service_minutes(point, days=7):
    """Rolling mean of actual service time over the last week; falls back
    to the configured default until there's enough history."""
    since = timezone.now() - timedelta(days=days)
    rows = QueueToken.objects.filter(service_point=point, completed_at__gte=since, started_at__isnull=False).values_list("started_at", "completed_at")[:500]
    mins = [(e - s).total_seconds() / 60 for s, e in rows if e > s]
    return round(sum(mins) / len(mins), 1) if len(mins) >= 5 else float(point.default_service_minutes)


def estimated_wait(point, token=None):
    waiting = QueueToken.objects.filter(service_point=point, token_date=timezone.localdate(), status=QueueToken.Status.WAITING)
    if token is not None:
        ahead = waiting.filter(number__lt=token.number).count() if not token.priority else waiting.filter(priority=True, number__lt=token.number).count()
    else:
        ahead = waiting.count()
    serving = QueueToken.objects.filter(service_point=point, token_date=timezone.localdate(), status__in=["called", "serving"]).exists()
    return {"ahead": ahead, "estimated_minutes": round((ahead + (1 if serving else 0)) * avg_service_minutes(point))}


def _consultation_board(point):
    """Doctor queues come from appointments (already tokenised at check-in)."""
    from apps.appointments.services import doctor_queue

    if point.doctor_id is None:
        return None
    q = doctor_queue(point.doctor, timezone.localdate())
    now = q["now_serving"]
    return {
        "now_serving": {"label": f"{point.token_prefix}{now.queue_token:03d}", "counter": point.counter_label} if now else None,
        "waiting": [f"{point.token_prefix}{a.queue_token:03d}" for a in q["waiting"][:8]],
        "waiting_count": len(q["waiting"]),
        "estimated_minutes": len(q["waiting"]) * (point.doctor.default_consultation_minutes or point.default_service_minutes),
    }


def board_for(point):
    if point.kind == ServicePoint.Kind.CONSULTATION and point.doctor_id:
        data = _consultation_board(point)
    else:
        today = QueueToken.objects.filter(service_point=point, token_date=timezone.localdate())
        now = today.filter(status__in=["called", "serving"]).order_by("-called_at").first()
        waiting = list(today.filter(status=QueueToken.Status.WAITING).order_by("-priority", "number")[:8])
        data = {
            "now_serving": {"label": now.label, "counter": point.counter_label} if now else None,
            "waiting": [t.label for t in waiting],
            "waiting_count": today.filter(status=QueueToken.Status.WAITING).count(),
            "estimated_minutes": estimated_wait(point)["estimated_minutes"],
        }
    return {"service_point": point.name, "kind": point.kind, "counter": point.counter_label, **(data or {})}


class ServicePointViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(ServicePoint, extra={"doctor_name": serializers.CharField(source="doctor.name", read_only=True, default=None)})
    queryset = ServicePoint.objects.select_related("doctor")
    filterset_fields = ["kind", "is_active", "doctor"]

    @action(detail=True, methods=["get"])
    def board(self, request, pk=None):
        return Response(board_for(self.get_object()))

    @action(detail=True, methods=["post"])
    def call_next(self, request, pk=None):
        """Completes whoever is being served, then calls the next waiting
        token (priority tokens first)."""
        point = self.get_object()
        today = timezone.localdate()
        with transaction.atomic():
            QueueToken.objects.filter(service_point=point, token_date=today, status__in=["called", "serving"]).update(
                status=QueueToken.Status.DONE, completed_at=timezone.now(),
            )
            nxt = QueueToken.objects.select_for_update().filter(service_point=point, token_date=today, status=QueueToken.Status.WAITING).order_by("-priority", "number").first()
            if nxt is None:
                return Response({"detail": "Queue is empty."}, status=404)
            nxt.status = QueueToken.Status.CALLED
            nxt.called_at = timezone.now()
            nxt.served_by = request.user
            nxt.save()
        if nxt.patient_id:
            from apps.clinical.notify import notify_patient

            notify_patient(nxt.patient, "token_called", {"token": nxt.label, "counter": point.counter_label or point.name})
        return Response(QueueTokenSerializer(nxt).data)


class QueueTokenSerializer(model_serializer(QueueToken, read_only=("number", "token_date", "status", "called_at", "started_at", "completed_at", "served_by", "recall_count"), extra={
    "service_point_name": serializers.CharField(source="service_point.name", read_only=True),
    "patient_name": serializers.CharField(source="patient.full_name", read_only=True, default=None),
})):
    label = serializers.CharField(read_only=True)
    wait = serializers.SerializerMethodField()

    def get_wait(self, obj):
        if obj.status != QueueToken.Status.WAITING:
            return None
        return estimated_wait(obj.service_point, obj)


class QueueTokenViewSet(TenantCRUDViewSet):
    serializer_class = QueueTokenSerializer
    queryset = QueueToken.objects.select_related("service_point", "patient")
    filterset_fields = ["service_point", "status", "token_date", "patient"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.request.query_params.get("token_date"):
            qs = qs.filter(token_date=timezone.localdate())
        return qs

    def perform_create(self, serializer):
        point = serializer.validated_data["service_point"]
        with transaction.atomic():
            last = QueueToken.objects.select_for_update().filter(service_point=point, token_date=timezone.localdate()).aggregate(m=Max("number"))["m"]
            serializer.validated_data["number"] = (last or 0) + 1
            super().perform_create(serializer)

    def _move(self, status, stamp):
        t = self.get_object()
        t.status = status
        if stamp:
            setattr(t, stamp, timezone.now())
        t.served_by = self.request.user
        t.save()
        return Response(self.get_serializer(t).data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._move(QueueToken.Status.SERVING, "started_at")

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return self._move(QueueToken.Status.DONE, "completed_at")

    @action(detail=True, methods=["post"])
    def skip(self, request, pk=None):
        return self._move(QueueToken.Status.SKIPPED, None)

    @action(detail=True, methods=["post"])
    def recall(self, request, pk=None):
        t = self.get_object()
        t.recall_count += 1
        t.status = QueueToken.Status.CALLED
        t.called_at = timezone.now()
        t.save()
        return Response(self.get_serializer(t).data)


class QueueDisplayViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(QueueDisplay, read_only=("key",))
    queryset = QueueDisplay.objects.prefetch_related("service_points")


class PublicQueueBoardView(APIView):
    """The TV screen polls this every few seconds. No login — the display
    key in the URL is the credential, and only token labels are exposed
    (never patient names)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, key):
        display = QueueDisplay.objects.filter(key=key).first()
        if display is None:
            return Response({"detail": "Unknown display."}, status=404)
        return Response({
            "display": display.name, "hospital": display.hospital.name, "announcement": display.announcement, "as_of": timezone.now(),
            "boards": [board_for(p) for p in display.service_points.filter(is_active=True)],
        })
