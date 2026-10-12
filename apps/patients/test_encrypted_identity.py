"""Patient name, date of birth and email are encrypted at rest (DPDP Act 2023,
NABH DAC.2.a (encryption at rest), AAC.1.f (duplicate detection still works).
IT (SPDI) Rules 2011) while the front desk can still find patients."""
from datetime import date

import pytest
from django.db import connection

from apps.patients.models import Patient


def raw_row(pk):
    with connection.cursor() as cursor:
        cursor.execute("SELECT first_name, last_name, date_of_birth, email, name_search FROM patients_patient WHERE id = %s", [pk])
        return cursor.fetchone()


@pytest.fixture
def asha(hospital):
    return Patient.objects.create(
        hospital=hospital, first_name="Ashwini", last_name="Rao-Iyer", mobile="9876500011",
        date_of_birth=date(1958, 3, 14), email="Ashwini.Rao@example.com",
    )


@pytest.mark.django_db
def test_stored_encrypted_and_read_back_as_normal(asha):
    first, last, dob, email, name_search = raw_row(asha.pk)
    for stored, plain in ((first, "Ashwini"), (last, "Rao-Iyer"), (dob, "1958-03-14"), (email, "Ashwini.Rao@example.com")):
        assert stored and plain not in stored
    assert "ashwini" not in name_search.lower()
    fresh = Patient.objects.get(pk=asha.pk)
    assert (fresh.first_name, fresh.last_name, fresh.date_of_birth, fresh.email) == ("Ashwini", "Rao-Iyer", date(1958, 3, 14), "Ashwini.Rao@example.com")
    assert fresh.birth_year == 1958 and fresh.full_name == "Ashwini Rao-Iyer"


@pytest.mark.django_db
def test_front_desk_search_by_name_prefix_and_email(auth_client, asha, hospital):
    Patient.objects.create(hospital=hospital, first_name="Ashok", last_name="Kumar", mobile="9876500012")
    Patient.objects.create(hospital=hospital, first_name="Rahul", last_name="Rao", mobile="9876500013")

    def found(term):
        return sorted(r["first_name"] for r in auth_client.get("/api/v1/patients/", {"search": term}).data["results"])

    assert found("ash") == ["Ashok", "Ashwini"]
    assert found("ASHW") == ["Ashwini"]
    assert found("rao") == ["Ashwini", "Rahul"]           # any word of the name
    assert found("ash rao") == ["Ashwini"]                # every word must match
    assert found("iyer") == ["Ashwini"]                   # hyphenated surnames split into words
    assert found("ashwini.rao@example.com") == ["Ashwini"]  # exact email, any case
    assert found("shwini") == []                          # prefixes only, not the middle of a word


@pytest.mark.django_db
def test_search_through_a_related_record(auth_client, asha):
    """Screens that search "patient__first_name" (e.g. the surgical pipeline) use the same index."""
    from apps.core.filters import EncryptedAwareSearchFilter
    from apps.opd.models import Encounter

    class View:
        search_fields = ["patient__first_name", "patient__last_name"]

    class Request:
        query_params = {"search": "ashw"}

    sql = str(EncryptedAwareSearchFilter().filter_queryset(Request(), Encounter.objects.all(), View()).query)
    assert "name_search" in sql and "first_name" not in sql.split("WHERE", 1)[1]


@pytest.mark.django_db
def test_duplicates_seniors_and_email_matching_still_work(asha, hospital):
    from apps.patients.registration import find_duplicates

    match = find_duplicates(hospital.pk, first_name="ashwini", last_name="rao-iyer", date_of_birth="1958-03-14", mobile="9876500011")[0]
    assert match["match"] == "exact" and set(match["reasons"]) >= {"same name", "same date of birth"}
    assert Patient.objects.filter(birth_year__lte=date.today().year - 60).get() == asha
    assert Patient.objects.by_email("ASHWINI.RAO@example.com").get() == asha


@pytest.mark.django_db
def test_upgrade_encrypts_existing_plaintext_rows(asha):
    """Rows written before this release hold plaintext; the migration re-saves them encrypted."""
    import importlib

    from django.apps import apps as django_apps

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE patients_patient SET first_name=%s, last_name=%s, date_of_birth=%s, email=%s, name_search='', birth_year=NULL WHERE id=%s",
            ["Meena", "Joshi", "1970-01-02", "meena@example.com", asha.pk],
        )
    legacy = Patient.objects.get(pk=asha.pk)
    assert legacy.first_name == "Meena" and legacy.date_of_birth == date(1970, 1, 2)  # plaintext still readable

    migration = importlib.import_module("apps.patients.migrations.0016_encrypt_name_dob_email")
    migration.encrypt_existing(django_apps, None)

    first, _, dob, email, _ = raw_row(asha.pk)
    assert "Meena" not in first and "1970" not in dob and "meena" not in email
    assert Patient.objects.search_name("mee").get().pk == asha.pk
    assert Patient.objects.get(pk=asha.pk).birth_year == 1970
