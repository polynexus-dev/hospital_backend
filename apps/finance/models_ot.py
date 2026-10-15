from django.db import models

from apps.core.models import TenantScopedModel

# Room categories an OT tariff can be set for: the facilities.Bed.BedType
# values. Blank = any room — the grade's default, also used for day-care /
# OPD surgery where the patient has no bed.
ROOM_CATEGORIES = ("general", "semi_private", "private", "icu", "ventilator")


class SurgeryGrade(TenantScopedModel):
    """Surgery grade master (e.g. Minor, Intermediate, Major, Super-major).
    A surgery request carries a grade; its OT tariff prices the surgery."""

    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "finance"
        ordering = ["sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["hospital", "code"], name="unique_surgery_grade_code")]

    def __str__(self):
        return self.name


class OTTariff(TenantScopedModel):
    """What a surgery of one grade costs in one room category, split into
    the surgeon's, anaesthetist's and assistant's fees and the OT charge.
    The row for the patient's room category wins; else the grade's
    any-room row (room_category blank)."""

    grade = models.ForeignKey(SurgeryGrade, on_delete=models.CASCADE, related_name="tariffs")
    room_category = models.CharField(max_length=16, blank=True, help_text="facilities.Bed.BedType value; blank = any room")
    surgeon_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    anaesthetist_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    ot_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    assistant_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "finance"
        ordering = ["grade__sort_order", "grade__name", "room_category"]
        constraints = [models.UniqueConstraint(fields=["hospital", "grade", "room_category"], name="unique_ot_tariff_grade_room")]

    @property
    def total(self):
        return self.surgeon_fee + self.anaesthetist_fee + self.ot_charge + self.assistant_fee

    def __str__(self):
        return f"{self.grade} – {self.room_category or 'any room'}"
