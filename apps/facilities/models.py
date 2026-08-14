from django.db import models

from apps.core.models import Department, TenantScopedModel


class Ward(TenantScopedModel):
    class WardType(models.TextChoices):
        GENERAL = "general", "General"
        SEMI_PRIVATE = "semi_private", "Semi-Private"
        PRIVATE = "private", "Private"
        ICU = "icu", "ICU"
        OT_PREP = "ot_prep", "OT Prep/Recovery"

    name = models.CharField(max_length=120)
    ward_type = models.CharField(max_length=16, choices=WardType.choices, default=WardType.GENERAL)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="wards")
    floor = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["hospital", "name"], name="unique_ward_name_per_hospital"),
        ]

    def __str__(self):
        return f"{self.name} ({self.hospital.name})"


class Room(TenantScopedModel):
    class RoomType(models.TextChoices):
        GENERAL = "general", "General"
        SEMI_PRIVATE = "semi_private", "Semi-Private"
        PRIVATE = "private", "Private"
        DELUXE = "deluxe", "Deluxe"

    ward = models.ForeignKey(Ward, on_delete=models.CASCADE, related_name="rooms")
    room_number = models.CharField(max_length=32)
    room_type = models.CharField(max_length=16, choices=RoomType.choices, default=RoomType.GENERAL)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["ward__name", "room_number"]
        constraints = [
            models.UniqueConstraint(fields=["ward", "room_number"], name="unique_room_number_per_ward"),
        ]

    def __str__(self):
        return f"{self.room_number} ({self.ward.name})"


class Bed(TenantScopedModel):
    class BedType(models.TextChoices):
        GENERAL = "general", "General"
        SEMI_PRIVATE = "semi_private", "Semi-Private"
        PRIVATE = "private", "Private"
        ICU = "icu", "ICU"
        VENTILATOR = "ventilator", "Ventilator"

    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        OCCUPIED = "occupied", "Occupied"
        MAINTENANCE = "maintenance", "Under Maintenance"
        RESERVED = "reserved", "Reserved"

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="beds")
    bed_number = models.CharField(max_length=32)
    bed_type = models.CharField(max_length=16, choices=BedType.choices, default=BedType.GENERAL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.AVAILABLE)
    # String FK, not a direct import — apps.ipd needs apps.facilities.Bed
    # (Admission.bed), so apps.facilities importing apps.ipd.Admission back
    # would be circular (same pattern as Prescription.encounter in
    # apps.patients). Set/cleared exclusively by apps.ipd.services
    # (admit_patient / transfer_ward / discharge_patient) — never edited
    # directly, same discipline as Bed.status.
    current_admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["room__ward__name", "room__room_number", "bed_number"]
        constraints = [
            models.UniqueConstraint(fields=["room", "bed_number"], name="unique_bed_number_per_room"),
        ]

    def __str__(self):
        return f"{self.bed_number} ({self.room})"
