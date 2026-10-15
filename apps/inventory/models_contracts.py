"""MedNet M2.22 procurement parity: vendor rate contracts (PO lines take
the contract rate), PO delivery schedules, and the indent / PO cart that
is kept between sessions. Imported by inventory.models."""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel


class RateContract(TenantScopedModel):
    """A vendor's agreed rates for items over a validity period."""

    vendor = models.ForeignKey("finance.Vendor", on_delete=models.PROTECT, related_name="rate_contracts")
    contract_number = models.CharField(max_length=40)
    valid_from = models.DateField()
    valid_to = models.DateField()
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        app_label = "inventory"
        ordering = ["-valid_from", "contract_number"]
        constraints = [models.UniqueConstraint(fields=["hospital", "contract_number"], name="unique_rate_contract_number")]

    def __str__(self):
        return f"{self.contract_number} ({self.vendor})"

    def is_valid_on(self, day):
        return self.is_active and self.valid_from <= day <= self.valid_to


class RateContractLine(TenantScopedModel):
    contract = models.ForeignKey(RateContract, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="contract_lines")
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    contracted_quantity = models.PositiveIntegerField(null=True, blank=True, help_text="Quantity agreed for the period; blank = open.")

    class Meta:
        app_label = "inventory"
        ordering = ["contract", "item__name"]
        constraints = [models.UniqueConstraint(fields=["contract", "item"], name="one_rate_per_item_per_contract")]


class PODelivery(TenantScopedModel):
    """Delivery schedule: how much of a PO line is due by when."""

    po_item = models.ForeignKey("inventory.POItem", on_delete=models.CASCADE, related_name="deliveries")
    due_date = models.DateField()
    quantity = models.PositiveIntegerField()

    class Meta:
        app_label = "inventory"
        ordering = ["due_date", "id"]


class ProcurementCart(TenantScopedModel):
    """A user's draft indent or PO, saved as they build it so it survives
    logging out, a closed tab or another device, until it's submitted.
    lines: [{item, quantity, unit_cost?, note?}]; header: kind-specific
    fields (indent: department, from_store; PO: vendor, po_number,
    expected_delivery)."""

    class Kind(models.TextChoices):
        INDENT = "indent", "Store indent"
        PO = "po", "Purchase order"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="procurement_carts")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    header = models.JSONField(default=dict, blank=True)
    lines = models.JSONField(default=list, blank=True)
    saved_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "inventory"
        constraints = [models.UniqueConstraint(fields=["user", "kind"], name="one_cart_per_user_per_kind")]
