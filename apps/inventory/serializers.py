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

    class Meta:
        model = POItem
        fields = ["id", "item", "item_name", "ordered_quantity", "received_quantity", "unit_cost"]
        read_only_fields = ["id"]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    po_items = POItemSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ["id", "po_number", "vendor_name", "status", "ordered_at", "po_items"]
        read_only_fields = ["id", "ordered_at"]


class StockTransactionSerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source="item.name")

    class Meta:
        model = StockTransaction
        fields = ["id", "item", "item_name", "transaction_type", "quantity", "reference", "transaction_date"]
        read_only_fields = ["id", "transaction_date"]
