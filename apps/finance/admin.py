from django.contrib import admin
from .models import Expense, Ledger, Receivable

admin.site.register(Ledger)
admin.site.register(Expense)
admin.site.register(Receivable)
