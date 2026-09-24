from decimal import Decimal

from .models import Bill


def recalculate_bill(bill: Bill) -> Bill:
    """Totals from the bill's items (GST shown separately and added to net),
    and — once the bill has left draft — its payment status from payments
    received, so adding charges to a paid bill re-opens it."""
    items = list(bill.items.all())
    bill.total_amount = sum((i.total_price for i in items), Decimal("0"))
    bill.tax_amount = sum((i.tax_amount for i in items), Decimal("0"))
    bill.net_amount = bill.total_amount + bill.tax_amount - Decimal(bill.discount_amount or 0)
    fields = ["total_amount", "tax_amount", "net_amount"]
    if bill.status in (Bill.Status.UNPAID, Bill.Status.PARTIALLY_PAID, Bill.Status.PAID):
        paid = sum((p.amount for p in bill.payments.all()), Decimal("0"))
        if paid <= 0:
            bill.status = Bill.Status.UNPAID
        elif paid >= bill.net_amount:
            bill.status = Bill.Status.PAID
        else:
            bill.status = Bill.Status.PARTIALLY_PAID
        fields.append("status")
    bill.save(update_fields=fields)
    return bill
