"""Duplicate patients (NABH AAC.1.f): seeding by encrypted mobile, and merging
exact duplicates without losing records."""
import pytest
from django.core.management import call_command

from apps.communications.models import ConsentOptOut
from apps.core.encryption import get_or_create_encrypted
from apps.patients.models import Patient


@pytest.mark.django_db
def test_get_or_create_finds_a_patient_by_encrypted_mobile(hospital):
    first, created = get_or_create_encrypted(Patient, hospital=hospital, mobile="9823012345", defaults={"first_name": "Asha", "last_name": "Patil"})
    again, created_again = get_or_create_encrypted(Patient, hospital=hospital, mobile="+91 98230 12345", defaults={"first_name": "Asha"})
    assert created and not created_again and again == first


# NABH AAC.1.f
@pytest.mark.django_db
def test_merge_duplicate_patients_keeps_the_oldest_and_moves_records(hospital):
    keep = Patient.objects.create(hospital=hospital, first_name="Ramesh", last_name="Deshmukh", mobile="9823023456")
    copy = Patient.objects.create(hospital=hospital, first_name="ramesh", last_name="Deshmukh ", mobile="9823023456")
    other = Patient.objects.create(hospital=hospital, first_name="Rekha", last_name="Deshmukh", mobile="9823023456")
    for p in (keep, copy):  # same unique opt-out on both: must not break the merge
        ConsentOptOut.objects.create(hospital=hospital, patient=p, channel="sms", purpose="marketing", is_opted_out=True)
    ConsentOptOut.objects.create(hospital=hospital, patient=copy, channel="email", purpose="marketing", is_opted_out=True)

    call_command("merge_duplicate_patients")  # dry run changes nothing
    assert Patient.objects.filter(pk=copy.pk).exists()

    call_command("merge_duplicate_patients", "--apply")
    assert set(Patient.objects.values_list("pk", flat=True)) == {keep.pk, other.pk}
    assert set(ConsentOptOut.objects.filter(patient=keep).values_list("channel", flat=True)) == {"sms", "email"}
