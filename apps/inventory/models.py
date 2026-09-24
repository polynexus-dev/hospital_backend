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
    vendor = models.ForeignKey("finance.Vendor", on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_orders")
    approved_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    expected_delivery = models.DateField(null=True, blank=True)
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
        TRANSFER = "transfer", "Store transfer"

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="stock_transactions")
    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)
    quantity = models.IntegerField()
    reference = models.CharField(max_length=150, blank=True)
    transaction_date = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-transaction_date"]

    def __str__(self):
        return f"StockTx [{self.transaction_type}] {self.quantity} of {self.item}"



# --- NABH FPM.1 procurement additions -------------------------------------------


class Store(TenantScopedModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20)
    is_main = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PurchaseApprovalRule(TenantScopedModel):
    """FPM.1.a — PO value bands and the role that must approve them."""

    min_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    max_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    approver_role = models.CharField(max_length=40, help_text="Role template, e.g. purchase_manager, finance_manager, owner")

    class Meta:
        ordering = ["min_amount"]


class StoreIndent(TenantScopedModel):
    """FPM.1.c department requisition. items: [{item, quantity, issued_quantity}]"""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        ISSUED = "issued", "Issued"
        REJECTED = "rejected", "Rejected"

    indent_number = models.CharField(max_length=30, blank=True, editable=False)
    department = models.CharField(max_length=100)
    from_store = models.ForeignKey(Store, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    items = models.JSONField(default=list)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, related_name="+")
    approved_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.indent_number and self.hospital_id:
            n = StoreIndent.objects.filter(hospital_id=self.hospital_id).count() + 1
            self.indent_number = f"SI{n:06d}"
        super().save(*args, **kwargs)


class GoodsReceiptNote(TenantScopedModel):
    """FPM.1.e — items: [{po_item, received_quantity, rejected_quantity, batch_number, expiry_date, remarks}]"""

    grn_number = models.CharField(max_length=30, blank=True, editable=False)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="grns")
    store = models.ForeignKey(Store, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    received_on = models.DateField(default=timezone.localdate)
    vendor_invoice_number = models.CharField(max_length=60, blank=True)
    items = models.JSONField(default=list)
    has_discrepancy = models.BooleanField(default=False, editable=False)
    discrepancy_notes = models.TextField(blank=True, editable=False)
    received_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-received_on", "-id"]

    def save(self, *args, **kwargs):
        if not self.grn_number and self.hospital_id:
            n = GoodsReceiptNote.objects.filter(hospital_id=self.hospital_id).count() + 1
            self.grn_number = f"GRN{n:06d}"
        super().save(*args, **kwargs)


class StockTransfer(TenantScopedModel):
    """FPM.1.b movement between stores / departments."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="transfers")
    from_store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="+")
    to_store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()
    transferred_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, related_name="+")
    transferred_at = models.DateTimeField(default=timezone.now)
    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-transferred_at"]


class SupplierRating(TenantScopedModel):
    """FPM.1.f quality feedback on purchased goods."""

    vendor_name = models.CharField(max_length=200)
    grn = models.ForeignKey(GoodsReceiptNote, on_delete=models.SET_NULL, null=True, blank=True, related_name="ratings")
    quality = models.PositiveSmallIntegerField()
    delivery_timeliness = models.PositiveSmallIntegerField()
    packaging = models.PositiveSmallIntegerField(default=3)
    remarks = models.TextField(blank=True)
    rated_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
