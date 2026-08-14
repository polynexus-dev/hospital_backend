from django.db import transaction

from .models import DispenseRecord, MedicineBatch, StockAdjustment


class InsufficientStock(Exception):
    pass


@transaction.atomic
def dispense_medicine(*, hospital, batch: MedicineBatch, quantity: int, dispensed_by, prescription=None) -> DispenseRecord:
    """select_for_update — same race-safety reasoning as every other stock/
    capacity-limited service in this codebase (apps.appointments.services.
    book_appointment, apps.ipd.services.admit_patient): two simultaneous
    dispenses against the same batch can't both succeed past its stock."""
    locked_batch = MedicineBatch.objects.select_for_update().get(pk=batch.pk)
    if locked_batch.quantity_available < quantity:
        raise InsufficientStock(
            f"Only {locked_batch.quantity_available} of {locked_batch.medicine} (batch {locked_batch.batch_number}) available, {quantity} requested."
        )

    locked_batch.quantity_available -= quantity
    locked_batch.save(update_fields=["quantity_available"])

    return DispenseRecord.objects.create(
        hospital=hospital, prescription=prescription, batch=locked_batch, quantity=quantity, dispensed_by=dispensed_by,
    )


@transaction.atomic
def adjust_stock(*, hospital, batch: MedicineBatch, adjustment_type: str, quantity_delta: int, reason: str, adjusted_by) -> StockAdjustment:
    locked_batch = MedicineBatch.objects.select_for_update().get(pk=batch.pk)
    new_quantity = locked_batch.quantity_available + quantity_delta
    if new_quantity < 0:
        raise InsufficientStock(
            f"Adjustment of {quantity_delta:+d} would take {locked_batch.medicine} (batch {locked_batch.batch_number}) below zero (currently {locked_batch.quantity_available})."
        )

    locked_batch.quantity_available = new_quantity
    locked_batch.save(update_fields=["quantity_available"])

    return StockAdjustment.objects.create(
        hospital=hospital, batch=locked_batch, adjustment_type=adjustment_type,
        quantity_delta=quantity_delta, reason=reason, adjusted_by=adjusted_by,
    )
