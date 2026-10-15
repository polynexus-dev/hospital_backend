from rest_framework import serializers
from .models import Item, ItemCategory, POItem, PurchaseOrder, StockLevel, StockTransaction


class ItemCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemCategory
        fields = ["id", "name", "code"]
        read_only_fields = ["id"]


class ItemSerializer(serializers.ModelSerializer):
    category_name = serializers.ReadOnlyField(source="category.name")

    class Meta:
        model = Item
        fields = ["id", "category", "category_name", "name", "code", "unit_of_measure", "min_stock_level"]
        read_only_fields = ["id"]


class StockLevelSerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source="item.name")

    class Meta:
        model = StockLevel
        fields = ["id", "item", "item_name", "batch_number", "expiry_date", "quantity_on_hand", "unit_cost"]
        read_only_fields = ["id"]


class POItemSerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source="item.name")
    contract_number = serializers.ReadOnlyField(source="contract_line.contract.contract_number", default="")
    deliveries = serializers.SerializerMethodField()

    class Meta:
        model = POItem
        fields = ["id", "item", "item_name", "ordered_quantity", "received_quantity", "unit_cost",
                  "contract_line", "contract_number", "contract_rate", "rate_override_reason", "deliveries"]
        read_only_fields = ["id", "contract_line", "contract_rate"]

    def get_deliveries(self, obj):
        return [{"due_date": d.due_date, "quantity": d.quantity} for d in obj.deliveries.all()]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    po_items = POItemSerializer(many=True, read_only=True)
    approved_by_name = serializers.ReadOnlyField(source="approved_by.get_full_name", default="")
    total_value = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = ["id", "po_number", "vendor", "vendor_name", "status", "ordered_at", "expected_delivery",
                  "approved_by", "approved_by_name", "approved_at", "total_value", "po_items"]
        read_only_fields = ["id", "ordered_at", "approved_by", "approved_at"]
        extra_kwargs = {"vendor_name": {"required": False}}

    def get_total_value(self, obj):
        return sum((i.ordered_quantity * i.unit_cost for i in obj.po_items.all()), 0)

    def validate_vendor(self, vendor):
        user = self.context["request"].user if self.context.get("request") else None
        if vendor is not None and user is not None and vendor.hospital_id != user.hospital_id:
            raise serializers.ValidationError("Refers to a record from a different hospital.")
        return vendor

    def validate(self, attrs):
        if attrs.get("vendor") and not attrs.get("vendor_name"):
            attrs["vendor_name"] = attrs["vendor"].name
        if not attrs.get("vendor_name") and self.instance is None:
            raise serializers.ValidationError({"vendor": "Choose the vendor."})
        return attrs


class StockTransactionSerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source="item.name")

    class Meta:
        model = StockTransaction
        fields = ["id", "item", "item_name", "transaction_type", "quantity", "reference", "transaction_date"]
        read_only_fields = ["id", "transaction_date"]
