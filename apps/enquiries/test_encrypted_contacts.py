"""Leads, blood donors and health-camp registrations keep names and contact
details encrypted at rest (DPDP Act 2023) and stay searchable. NABH DAC.2.a."""
from datetime import date

import pytest
from django.db import connection

from apps.bloodbank.models import Donor
from apps.enquiries.models import Enquiry
from apps.enquiries.services import find_duplicates
from apps.packages.models import Campaign, CampRegistration


def raw(table, columns, pk):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE id = %s", [pk])
        return cursor.fetchone()


@pytest.mark.django_db
def test_leads_are_encrypted_searchable_and_deduplicated(auth_client, hospital):
    lead = Enquiry.objects.create(hospital=hospital, name="Meena Kulkarni", mobile="+91 98765 43210", alternate_mobile="9123456780", email="Meena@Example.com", source="walk_in")
    stored = raw("enquiries_enquiry", ["name", "mobile", "alternate_mobile", "email"], lead.pk)
    assert all("Meena" not in v and "98765" not in v and "example" not in v.lower() for v in stored)
    assert Enquiry.objects.get(pk=lead.pk).name == "Meena Kulkarni"

    assert list(find_duplicates(hospital, mobile="9876543210")) == [lead]           # same number, other format
    assert list(find_duplicates(hospital, alternate_mobile="09123456780")) == [lead]
    assert list(find_duplicates(hospital, email="meena@example.com")) == [lead]

    def found(term):
        return [r["id"] for r in auth_client.get("/api/v1/enquiries/", {"search": term}).data["results"]]

    assert found("meen") == [lead.pk] and found("kulk") == [lead.pk]
    assert found("9876543210") == [lead.pk] and found("meena@example.com") == [lead.pk]
    assert found("xyz") == []


@pytest.mark.django_db
def test_blood_donors_are_encrypted_and_searchable(hospital):
    donor = Donor.objects.create(hospital=hospital, name="Ravi Shinde", blood_group="O+", phone="9000011122", date_of_birth=date(1990, 4, 2))
    stored = raw("bloodbank_donor", ["name", "phone", "date_of_birth"], donor.pk)
    assert "Ravi" not in stored[0] and "9000011122" not in stored[1] and "1990" not in stored[2]
    fresh = Donor.objects.get(pk=donor.pk)
    assert (fresh.name, fresh.phone, fresh.date_of_birth) == ("Ravi Shinde", "9000011122", date(1990, 4, 2))
    from apps.core.encryption import name_query_tokens

    assert Donor.objects.filter(name_search__contains=f" {name_query_tokens('ravi')[0]} ").get() == donor


@pytest.mark.django_db
def test_camp_registrations_are_encrypted(hospital):
    from datetime import timedelta

    from django.utils import timezone

    camp = Campaign.objects.create(hospital=hospital, name="Diabetes camp", start_date=timezone.localdate(), end_date=timezone.localdate() + timedelta(days=1))
    reg = CampRegistration.objects.create(hospital=hospital, campaign=camp, patient_name="Sunil Patil", mobile="9888877777")
    stored = raw("packages_campregistration", ["patient_name", "mobile"], reg.pk)
    assert "Sunil" not in stored[0] and "9888877777" not in stored[1]
    assert reg.mobile_hash and reg.name_search


@pytest.mark.django_db
def test_upgrade_encrypts_existing_leads(hospital):
    import importlib

    from django.apps import apps as django_apps

    lead = Enquiry.objects.create(hospital=hospital, name="placeholder", mobile="9000000000", source="walk_in")
    with connection.cursor() as cursor:
        cursor.execute("UPDATE enquiries_enquiry SET name=%s, mobile=%s, mobile_hash='', name_search='' WHERE id=%s", ["Asha Rao", "9811111111", lead.pk])
    importlib.import_module("apps.enquiries.migrations.0008_encrypt_personal_data").encrypt_existing(django_apps, None)
    assert "Asha" not in raw("enquiries_enquiry", ["name"], lead.pk)[0]
    assert list(find_duplicates(hospital, mobile="9811111111")) == [Enquiry.objects.get(pk=lead.pk)]
