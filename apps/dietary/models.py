"""
Dietary & kitchen (NABH COP.7.a/b): nutritional screening (via
clinical.AssessmentTemplate category "dietary"), dietitian consultation,
therapeutic diet orders for inpatients, kitchen menus, and meal service
with delivery tracking.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel

MEALS = [
    ("early_morning", "Early morning"), ("breakfast", "Breakfast"), ("mid_morning", "Mid-morning"),
    ("lunch", "Lunch"), ("evening", "Evening snack"), ("dinner", "Dinner"), ("bedtime", "Bedtime"),
]


class DietType(TenantScopedModel):
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=20)
    description = models.TextField(blank=True)
    is_therapeutic = models.BooleanField(default=True)
    default_calories = models.PositiveIntegerField(null=True, blank=True)
    default_protein_g = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["hospital", "code"], name="unique_diet_code")]

    def __str__(self):
        return self.name


class DietOrder(TenantScopedModel):
    class Texture(models.TextChoices):
        REGULAR = "regular", "Regular"
        SOFT = "soft", "Soft"
        MINCED = "minced", "Minced"
        PUREED = "pureed", "Pureed"
        LIQUID = "liquid", "Liquid"
        RT_FEED = "rt_feed", "Ryle's tube feed"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        NPO = "npo", "Nil by mouth"
        STOPPED = "stopped", "Stopped"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="diet_orders")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.CASCADE, related_name="diet_orders")
    diet_type = models.ForeignKey(DietType, on_delete=models.PROTECT, related_name="orders")
    texture = models.CharField(max_length=10, choices=Texture.choices, default=Texture.REGULAR)
    preference = models.CharField(max_length=20, blank=True, help_text="veg | non_veg | eggetarian | jain | vegan")
    calories = models.PositiveIntegerField(null=True, blank=True)
    protein_g = models.PositiveIntegerField(null=True, blank=True)
    fluid_restriction_ml = models.PositiveIntegerField(null=True, blank=True)
    food_allergies = models.CharField(max_length=255, blank=True)
    instructions = models.TextField(blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)
    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-start_date", "-id"]


class DietConsultation(TenantScopedModel):
    """COP.7.a — dietitian assessment & plan."""

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="diet_consultations")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    bmi = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, editable=False)
    nutritional_risk = models.CharField(max_length=20, blank=True, help_text="low | moderate | high")
    assessment = models.TextField()
    plan = models.TextField()
    follow_up_on = models.DateField(null=True, blank=True)
    dietitian = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.height_cm and self.weight_kg:
            m = float(self.height_cm) / 100
            self.bmi = round(float(self.weight_kg) / (m * m), 1)
        super().save(*args, **kwargs)


class MenuItem(TenantScopedModel):
    """What the kitchen serves for a diet type at each meal."""

    diet_type = models.ForeignKey(DietType, on_delete=models.CASCADE, related_name="menu")
    meal = models.CharField(max_length=14, choices=MEALS)
    weekday = models.PositiveSmallIntegerField(null=True, blank=True, help_text="0=Mon … 6=Sun; blank = every day.")
    items = models.CharField(max_length=300)
    is_veg = models.BooleanField(default=True)
    calories = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["diet_type", "meal"]


class MealService(TenantScopedModel):
    """One tray to one patient for one meal — the kitchen's delivery record."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        PREPARED = "prepared", "Prepared"
        DELIVERED = "delivered", "Delivered"
        REFUSED = "refused", "Refused by patient"
        HELD = "held", "Held (NPO / procedure)"

    diet_order = models.ForeignKey(DietOrder, on_delete=models.CASCADE, related_name="services")
    service_date = models.DateField(default=timezone.localdate)
    meal = models.CharField(max_length=14, choices=MEALS)
    items = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PLANNED)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    consumption_pct = models.PositiveSmallIntegerField(null=True, blank=True)
    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["service_date", "meal"]
        constraints = [models.UniqueConstraint(fields=["diet_order", "service_date", "meal"], name="one_tray_per_meal")]
