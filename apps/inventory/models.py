from django.db import models
from django.utils import timezone
from apps.core.models import TenantScopedModel


class ItemCategory(TenantScopedModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class Item(TenantScopedModel):
    category = models.ForeignKey(ItemCategory, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=50)
    unit_of_measure = models.CharField(max_length=50, default="pcs")
    min_stock_level = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} [{self.code}]"


class StockLevel(TenantScopedModel):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="stock_levels")
    batch_number = models.CharField(max_length=100)
    expiry_date = models.DateField(null=True, blank=True)
    quantity_on_hand = models.IntegerField(default=0)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["item", "expiry_date"]

    def __str__(self):
        return f"Stock for {self.item} (Batch: {self.batch_number}): {self.quantity_on_hand}"


class PurchaseOrder(TenantScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    po_number = models.CharField(max_length=100)
    vendor_name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    ordered_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-ordered_at"]

    def __str__(self):
        return f"PO #{self.po_number} ({self.vendor_name}) - {self.status}"


class POItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="po_items")
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    ordered_quantity = models.PositiveIntegerField()
    received_quantity = models.PositiveIntegerField(default=0)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"POItem for {self.item}: {self.ordered_quantity} pcs @ {self.unit_cost}"


class StockTransaction(TenantScopedModel):
    class TransactionType(models.TextChoices):
        RECEIPT = "receipt", "Receipt"
        ISSUE = "issue", "Issue"
        ADJUSTMENT = "adjustment", "Adjustment"
        RETURN = "return", "Return"

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="stock_transactions")
    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)
    quantity = models.IntegerField()
    reference = models.CharField(max_length=150, blank=True)
    transaction_date = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-transaction_date"]

    def __str__(self):
        return f"StockTx [{self.transaction_type}] {self.quantity} of {self.item}"
