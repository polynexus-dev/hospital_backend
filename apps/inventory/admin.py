from django.contrib import admin
from .models import Item, ItemCategory, POItem, PurchaseOrder, StockLevel, StockTransaction

admin.site.register(ItemCategory)
admin.site.register(Item)
admin.site.register(StockLevel)
admin.site.register(PurchaseOrder)
admin.site.register(POItem)
admin.site.register(StockTransaction)
