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


IMPORT_COLUMNS = ["Item", "Type", "Group", "Note"]
_GROUPS = {"i": "list_1", "1": "list_1", "list i": "list_1", "ii": "list_2", "2": "list_2", "list ii": "list_2",
           "iii": "list_3", "3": "list_3", "list iii": "list_3", "iv": "list_4", "4": "list_4", "list iv": "list_4", "other": "other"}


def import_non_payable_rows(npl, rows, replace=False):
    """Matches each row to a medicine (by name), a service tariff (by code or
    name) or keeps it as a keyword, and adds it to the list."""
    from django.db import transaction
    from django.db.models import Q

    from apps.finance.models import ServiceTariff
    from apps.pharmacy.models import Medicine

    if rows and rows[0] and rows[0][0].strip().lower() in ("item", "item name", "name"):
        rows = rows[1:]
    added, duplicates, errors = 0, 0, []
    hid = npl.hospital_id
    with transaction.atomic():
        if replace:
            npl.items.all().delete()
        for n, row in enumerate(rows, start=2):
            row = list(row) + ["", "", "", ""]
            item, kind, group_raw, note = (str(x).strip() for x in row[:4])
            if not item:
                continue
            group = _GROUPS.get(group_raw.lower(), "list_1" if not group_raw else None)
            if group is None:
                errors.append({"row": n, "item": item, "error": f"Unknown group '{group_raw}' (use I, II, III, IV or other)."})
                continue
            kind = kind.lower()
            med = tariff = None
            if kind in ("", "medicine", "drug"):
                med = Medicine.objects.filter(hospital_id=hid, name__iexact=item).first()
            if med is None and kind in ("", "service", "tariff"):
                tariff = ServiceTariff.objects.filter(hospital_id=hid).filter(Q(code__iexact=item) | Q(name__iexact=item)).first()
            if kind in ("medicine", "drug") and med is None:
                errors.append({"row": n, "item": item, "error": "No medicine with this name."})
                continue
            if kind in ("service", "tariff") and tariff is None:
                errors.append({"row": n, "item": item, "error": "No service with this code or name."})
                continue
            match = {"medicine": med} if med else {"tariff": tariff} if tariff else {"keyword": item[:100]}
            lookup = {k: v for k, v in match.items()} if "keyword" not in match else {"keyword__iexact": match["keyword"]}
            if npl.items.filter(**lookup).exists():
                duplicates += 1
                continue
            NonPayableItem.objects.create(hospital_id=hid, non_payable_list=npl, group=group, note=note[:255], **match)
            added += 1
    return {"added": added, "already_on_list": duplicates, "errors": errors, "total_items": npl.items.count()}


class NonPayableListViewSet(_RecheckOpenBillsMixin, TenantCRUDViewSet):
    serializer_class = model_serializer(NonPayableList, extra={
        "tpa_name": serializers.CharField(source="tpa_company.name", read_only=True, default="All insurers"),
        "item_count": serializers.IntegerField(source="items.count", read_only=True),
    })
    queryset = NonPayableList.objects.select_related("tpa_company").prefetch_related("items")
    filterset_fields = ["tpa_company", "is_active"]
    audited_fields = ("name", "tpa_company", "applies_to_categories", "is_active")

    @action(detail=False, methods=["get"], url_path="import-template")
    def import_template(self, request):
        """The .xlsx layout the import expects, with example rows."""
        from django.http import HttpResponse

        from apps.core.xlsx import build_xlsx

        rows = [IMPORT_COLUMNS,
                ["Gloves (sterile)", "keyword", "I", "Consumables are not payable"],
                ["Pan 40", "medicine", "IV", ""],
                ["NURS-GEN", "service", "II", "Nursing is part of the room rent"]]
        resp = HttpResponse(build_xlsx(rows, "Non-payable items"), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = 'attachment; filename="non-payable-items-template.xlsx"'
        return resp

    @action(detail=True, methods=["post"], url_path="import")
    def import_items(self, request, pk=None):
        """POST multipart file=<.xlsx|.csv>[, replace=1] — adds the rows as
        items of this list. Columns: Item, Type (medicine | service | keyword;
        blank = work it out), Group (I-IV or other), Note. Rows that match
        nothing, or are already on the list, are reported back."""
        from apps.core.xlsx import SpreadsheetError, read_table

        if not request.user.has_perm("tpa.add_nonpayableitem"):
            return Response({"detail": "You don't have permission to add non-payable items."}, status=status.HTTP_403_FORBIDDEN)
        npl = self.get_object()
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": "Attach an .xlsx or .csv file."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            rows = read_table(upload)
        except SpreadsheetError as exc:
            return Response({"file": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        result = import_non_payable_rows(npl, rows, replace=str(request.data.get("replace", "")).lower() in ("1", "true", "yes"))
        self._log("update", npl)
        self._recheck()
        return Response(result, status=status.HTTP_200_OK)


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
