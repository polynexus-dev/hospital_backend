"""Rate contracts + contract-vs-actual report, and the indent / PO cart."""
import csv
import io
from datetime import date

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import TenantCRUDViewSet, model_serializer

from . import contracts
from .models_contracts import ProcurementCart, RateContract, RateContractLine


class RateContractLineSerializer(model_serializer(RateContractLine, extra={
    "item_name": serializers.CharField(source="item.name", read_only=True),
    "contract_number": serializers.CharField(source="contract.contract_number", read_only=True),
})):
    def validate_rate(self, value):
        if value < 0:
            raise serializers.ValidationError("Can't be negative.")
        return value


class RateContractSerializer(model_serializer(RateContract, extra={
    "vendor_name": serializers.CharField(source="vendor.name", read_only=True),
    "lines": RateContractLineSerializer(many=True, read_only=True),
})):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        start = attrs.get("valid_from", getattr(self.instance, "valid_from", None))
        end = attrs.get("valid_to", getattr(self.instance, "valid_to", None))
        if start and end and end < start:
            raise serializers.ValidationError({"valid_to": "Ends before it starts."})
        return attrs


class RateContractViewSet(TenantCRUDViewSet):
    serializer_class = RateContractSerializer
    queryset = RateContract.objects.select_related("vendor").prefetch_related("lines__item")
    filterset_fields = ["vendor", "is_active"]
    search_fields = ["contract_number", "vendor__name"]
    audited_fields = ("valid_from", "valid_to", "is_active")

    @action(detail=False, methods=["get"])
    def mis(self, request):
        """GET ?start=&end=&vendor=&output=csv|xlsx — contract vs actual, off-contract buying, contracts expiring in 30 days."""
        q = request.query_params
        try:
            end = date.fromisoformat(q["end"]) if q.get("end") else timezone.localdate()
            start = date.fromisoformat(q["start"]) if q.get("start") else end.replace(day=1)
        except ValueError:
            return Response({"detail": "start/end must be YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        data = contracts.contract_mis(request.user.hospital_id, start, end, q.get("vendor") or None)
        if q.get("output") in ("csv", "xlsx"):
            head = ["Contract", "Vendor", "Item", "Contract rate", "Contracted qty", "Ordered qty", "Received qty", "Average PO rate", "Variance (Rs)", "Utilisation %", "Lines above contract"]
            table = [[f"Rate contract MIS {start} to {end}"], head] + [
                [r["contract"], r["vendor"], r["item"], float(r["contract_rate"]), r["contracted_quantity"], r["ordered_quantity"], r["received_quantity"],
                 float(r["average_rate"]) if r["average_rate"] is not None else "", float(r["variance_value"]), r["utilisation_percent"], r["above_contract_lines"]]
                for r in data["lines"]]
            if data["off_contract"]:
                table += [[], ["Off-contract purchases"], ["PO", "Date", "Vendor", "Item", "Qty", "Rate", "Best contract rate", "Contract", "Contract vendor", "Extra cost (Rs)"]] + [
                    [o["po_number"], str(o["ordered_on"]), o["vendor"], o["item"], o["quantity"], float(o["rate"]), float(o["best_contract_rate"]), o["best_contract"],
                     o["best_contract_vendor"], float(o["extra_cost"])] for o in data["off_contract"]]
            name = f"rate-contract-mis-{start}-to-{end}"
            if q["output"] == "xlsx":
                from apps.core.xlsx import build_xlsx

                resp = HttpResponse(build_xlsx(table, "Rate contracts"), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                resp["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
                return resp
            buf = io.StringIO()
            csv.writer(buf).writerows(table)
            resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="{name}.csv"'
            return resp
        return Response({"start": start, "end": end, **data})

    @action(detail=False, methods=["get"])
    def rate(self, request):
        """GET ?vendor=&item=&date= — the contract rate that a PO line would take."""
        day = timezone.localdate()
        if request.query_params.get("date"):
            try:
                day = date.fromisoformat(request.query_params["date"])
            except ValueError:
                return Response({"date": "Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        line = contracts.contract_line_for(request.user.hospital_id, request.query_params.get("vendor"), request.query_params.get("item"), day)
        if line is None:
            return Response({"contract": None})
        return Response({"contract": line.contract.contract_number, "contract_line": line.pk, "rate": line.rate, "valid_to": line.contract.valid_to})


class RateContractLineViewSet(TenantCRUDViewSet):
    serializer_class = RateContractLineSerializer
    queryset = RateContractLine.objects.select_related("contract", "item")
    filterset_fields = ["contract", "item"]
    audited_fields = ("rate", "contracted_quantity")


class CartView(APIView):
    """GET / PUT {header, lines} — the user's own indent or PO cart, kept until submitted."""

    permission_classes = [IsAuthenticated]

    def _kind(self, kind):
        if kind not in ProcurementCart.Kind.values:
            raise serializers.ValidationError({"kind": "indent | po"})
        return kind

    def _data(self, cart):
        return {"kind": cart.kind, "header": cart.header, "lines": cart.lines, "saved_at": cart.saved_at}

    def get(self, request, kind):
        return Response(self._data(contracts.get_cart(request.user, self._kind(kind))))

    def put(self, request, kind):
        cart = contracts.save_cart(request.user, self._kind(kind), request.data.get("header") or {}, request.data.get("lines") or [])
        return Response(self._data(cart))


class CartSubmitView(APIView):
    """POST — turn the cart into an indent / draft PO (needs the matching add permission)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, kind):
        perm = {"indent": "inventory.add_storeindent", "po": "inventory.add_purchaseorder"}.get(kind)
        if perm is None:
            return Response({"kind": "indent | po"}, status=status.HTTP_400_BAD_REQUEST)
        if not request.user.has_perm(perm):
            from apps.accounts.permission_catalog import is_hospital_admin

            if not is_hospital_admin(request.user):
                return Response({"detail": "You can't raise these."}, status=status.HTTP_403_FORBIDDEN)
        record = contracts.submit_cart(request.user, kind)
        if kind == "indent":
            return Response({"kind": kind, "id": record.pk, "number": record.indent_number}, status=status.HTTP_201_CREATED)
        return Response({"kind": kind, "id": record.pk, "number": record.po_number, "lines": record.po_items.count()}, status=status.HTTP_201_CREATED)
