from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import Role, assign_role
from apps.core.models import Hospital
from .models import Item, ItemCategory, PurchaseOrder, StockLevel

User = get_user_model()


class InventoryComprehensiveTestCase(TestCase):
    def setUp(self):
        self.hospital_a = Hospital.objects.create(name="Inv Hosp A", slug="inv-hosp-a")
        self.hospital_b = Hospital.objects.create(name="Inv Hosp B", slug="inv-hosp-b")

        self.user_a = User.objects.create_user(email="inv_mgr@hospa.com", password="password123", hospital=self.hospital_a)
        self.user_b = User.objects.create_user(email="inv_mgr@hospb.com", password="password123", hospital=self.hospital_b)

        self.role_inv = Role.objects.create(
            hospital=self.hospital_a,
            name="Inventory Manager",
            template=Role.Template.INVENTORY_MANAGER,
            data_scope=Role.DataScope.ALL,
        )
        assign_role(self.user_a, self.role_inv)

        self.cat_a = ItemCategory.objects.create(hospital=self.hospital_a, name="Consumables", code="CONS")
        self.item_a = Item.objects.create(hospital=self.hospital_a, category=self.cat_a, name="Gloves Medium", code="GLV-M")
        self.item_b = Item.objects.create(hospital=self.hospital_b, category=ItemCategory.objects.create(hospital=self.hospital_b, name="Med", code="MED"), name="Syringe 5ml", code="SYR-5")

    def test_tenant_isolation(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        res = client.get("/api/v1/inventory/items/")
        self.assertEqual(res.status_code, 200)
        results = res.data["results"] if isinstance(res.data, dict) else res.data
        item_ids = [i["id"] for i in results]
        self.assertIn(self.item_a.id, item_ids)
        self.assertNotIn(self.item_b.id, item_ids)

    def test_purchase_order_add_item(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)

        po = PurchaseOrder.objects.create(hospital=self.hospital_a, po_number="PO-1001", vendor_name="Surgical Supplies Co.")
        res = client.post(f"/api/v1/inventory/purchase-orders/{po.id}/add-item/", {
            "item": self.item_a.id,
            "ordered_quantity": 500,
            "unit_cost": 12.50,
        })
        self.assertEqual(res.status_code, 201)

        po.refresh_from_db()
        self.assertEqual(po.po_items.count(), 1)
