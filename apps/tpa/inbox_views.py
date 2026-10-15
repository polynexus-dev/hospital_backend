"""Insurance inbox and correspondence, non-payable item lists, NHCX
set-up and pre-auth submission."""
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import TenantCRUDViewSet, model_serializer

from . import inbox, nhcx
from .models import Claim, ClaimMessage, NHCXProfile, NonPayableItem, NonPayableList, PreAuthRequest, TPAMailbox


class ClaimMessageSerializer(model_serializer(ClaimMessage, read_only=("resolved_at", "recorded_by", "source", "email_from", "email_message_id", "needs_review"), extra={
    "kind_label": serializers.CharField(source="get_kind_display", read_only=True),
    "direction_label": serializers.CharField(source="get_direction_display", read_only=True),
    "recorded_by_name": serializers.CharField(source="recorded_by.get_full_name", read_only=True, default=""),
    "is_open": serializers.BooleanField(read_only=True),
})):
    pass


class ClaimMessageViewSet(TenantCRUDViewSet):
    """Correspondence on a pre-auth or claim (?preauth_request= / ?claim= gives its thread)."""

    serializer_class = ClaimMessageSerializer
    queryset = ClaimMessage.objects.select_related("recorded_by")
    filterset_fields = ["preauth_request", "claim", "direction", "kind"]
    http_method_names = ["get", "post", "head", "options"]  # the record of what was said isn't edited

    def perform_create(self, serializer):
        msg = ClaimMessage(**serializer.validated_data, hospital=self.request.user.hospital, recorded_by=self.request.user)
        inbox.record(msg)
        serializer.instance = msg
        self._log("create", msg)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """POST {preauth_request? | claim?, kind?, amount?} — confirm an imported email waiting for review."""
        msg = self.get_object()
        hid = request.user.hospital_id
        pa = PreAuthRequest.objects.filter(hospital_id=hid, pk=request.data.get("preauth_request") or 0).first()
        claim = Claim.objects.filter(hospital_id=hid, pk=request.data.get("claim") or 0).first()
        kind = request.data.get("kind") or None
        if kind and kind not in ClaimMessage.Kind.values:
            return Response({"kind": "Unknown type."}, status=status.HTTP_400_BAD_REQUEST)
        inbox.confirm(msg, request.user, preauth_request=pa, claim=claim, kind=kind, amount=request.data.get("amount"))
        self._log("update", msg)
        return Response(self.get_serializer(msg).data)

    @action(detail=True, methods=["post"])
    def dismiss(self, request, pk=None):
        msg = self.get_object()
        if not msg.needs_review:
            return Response({"detail": "This message isn't waiting for review."}, status=status.HTTP_400_BAD_REQUEST)
        inbox.dismiss(msg, request.user)
        return Response(self.get_serializer(msg).data)

    @action(detail=False, methods=["get"], url_path="inbox")
    def inbox_view(self, request):
        """GET ?tpa= — open queries, pre-auths waiting past TAT, ageing claims."""
        return Response(inbox.inbox(request.user.hospital_id, request.query_params.get("tpa") or None))


class _RecheckOpenBillsMixin:
    """Any change to the lists re-flags open bills (apps.tpa.non_payable.recheck_open_bills)."""

    def _recheck(self):
        from .non_payable import recheck_open_bills

        recheck_open_bills(self.request.user.hospital_id)

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._recheck()

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self._recheck()

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        self._recheck()


class NonPayableListViewSet(_RecheckOpenBillsMixin, TenantCRUDViewSet):
    serializer_class = model_serializer(NonPayableList, extra={
        "tpa_name": serializers.CharField(source="tpa_company.name", read_only=True, default="All insurers"),
        "item_count": serializers.IntegerField(source="items.count", read_only=True),
    })
    queryset = NonPayableList.objects.select_related("tpa_company").prefetch_related("items")
    filterset_fields = ["tpa_company", "is_active"]
    audited_fields = ("name", "tpa_company", "applies_to_categories", "is_active")


class NonPayableItemSerializer(model_serializer(NonPayableItem, extra={
    "list_name": serializers.CharField(source="non_payable_list.name", read_only=True),
    "group_label": serializers.CharField(source="get_group_display", read_only=True),
    "matches": serializers.CharField(source="label", read_only=True),
})):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        get = lambda k: attrs.get(k, getattr(self.instance, k, None))  # noqa: E731
        if sum(1 for k in ("tariff", "medicine", "keyword") if get(k)) != 1:
            raise serializers.ValidationError({"keyword": "Match on exactly one of: a service tariff, a medicine or a keyword."})
        return attrs


class NonPayableItemViewSet(_RecheckOpenBillsMixin, TenantCRUDViewSet):
    serializer_class = NonPayableItemSerializer
    queryset = NonPayableItem.objects.select_related("non_payable_list", "tariff", "medicine")
    filterset_fields = ["non_payable_list", "group"]
    search_fields = ["keyword", "tariff__name", "medicine__name"]
    audited_fields = ("group", "tariff", "medicine", "keyword")


class NHCXProfileView(APIView):
    """GET / PUT the hospital's NHCX participant code and registry IDs."""

    def get(self, request):
        p = nhcx.profile_for(request.user.hospital_id)
        return Response({"participant_code": p.participant_code, "hfr_id": p.hfr_id, "rohini_id": p.rohini_id})

    def put(self, request):
        if not request.user.has_perm("tpa.change_tpacompany"):
            from apps.accounts.permission_catalog import is_hospital_admin

            if not is_hospital_admin(request.user):
                return Response({"detail": "You can't change the NHCX set-up."}, status=status.HTTP_403_FORBIDDEN)
        p, _ = NHCXProfile.objects.get_or_create(hospital_id=request.user.hospital_id)
        for f in ("participant_code", "hfr_id", "rohini_id"):
            if f in request.data:
                setattr(p, f, str(request.data[f] or "").strip()[:100])
        p.save()
        return self.get(request)


class PreAuthNHCXMixin:
    """GET nhcx/ — readiness + the FHIR bundle; POST nhcx-submit/ — send it."""

    @action(detail=True, methods=["get"])
    def nhcx(self, request, pk=None):
        pa = self.get_object()
        missing = nhcx.missing_fields(pa)
        return Response({"ready": not missing, "missing": missing, "bundle": nhcx.build_bundle(pa)})

    @action(detail=True, methods=["post"], url_path="nhcx-submit")
    def nhcx_submit(self, request, pk=None):
        pa = self.get_object()
        missing = nhcx.missing_fields(pa)
        if missing:
            return Response({"detail": "The pre-auth isn't complete.", "missing": missing}, status=status.HTTP_400_BAD_REQUEST)
        tx, error = nhcx.submit(pa, request.user)
        body = {"transaction": tx.pk, "status": tx.status, "nhcx_transaction_id": tx.nhcx_transaction_id}
        if error:
            return Response({**body, "detail": error}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        ClaimMessage.objects.create(hospital_id=pa.hospital_id, preauth_request=pa, direction=ClaimMessage.Direction.OUTGOING, kind=ClaimMessage.Kind.NOTE,
                                    subject=f"Submitted over NHCX ({tx.nhcx_transaction_id})", recorded_by=request.user)
        return Response(body, status=status.HTTP_201_CREATED)


class TPAMailboxView(APIView):
    """GET / PUT the insurer mailbox polled into the inbox; POST check/ to fetch now. The password is write-only."""

    FIELDS = ("host", "port", "use_ssl", "username", "folder", "is_active")

    def _can_change(self, request):
        from apps.accounts.permission_catalog import is_hospital_admin

        return request.user.has_perm("tpa.change_tpacompany") or is_hospital_admin(request.user)

    def _data(self, mb):
        if mb is None:
            return {"configured": False}
        return {"configured": True, **{f: getattr(mb, f) for f in self.FIELDS}, "has_password": bool(mb.password),
                "last_checked_at": mb.last_checked_at, "last_error": mb.last_error}

    def get(self, request):
        return Response(self._data(TPAMailbox.objects.filter(hospital_id=request.user.hospital_id).first()))

    def put(self, request):
        if not self._can_change(request):
            return Response({"detail": "You can't change the mailbox."}, status=status.HTTP_403_FORBIDDEN)
        mb = TPAMailbox.objects.filter(hospital_id=request.user.hospital_id).first() or TPAMailbox(hospital_id=request.user.hospital_id)
        for f in self.FIELDS:
            if f in request.data:
                setattr(mb, f, request.data[f])
        if request.data.get("password"):
            mb.password = request.data["password"]
        if not mb.host or not mb.username or not mb.password:
            return Response({"detail": "Host, username and password are needed."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            mb.port = int(mb.port)
        except (TypeError, ValueError):
            return Response({"port": "A number."}, status=status.HTTP_400_BAD_REQUEST)
        mb.save()
        return Response(self._data(mb))

    def post(self, request):
        """Fetch now."""
        from .mailbox import poll

        mb = TPAMailbox.objects.filter(hospital_id=request.user.hospital_id, is_active=True).first()
        if mb is None:
            return Response({"detail": "No active mailbox."}, status=status.HTTP_400_BAD_REQUEST)
        n = poll(mb)
        return Response({"imported": n, **self._data(mb)})
