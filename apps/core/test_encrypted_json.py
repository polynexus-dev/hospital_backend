import json

from apps.core.encryption import encrypt_value
from apps.patients.models import Patient, Prescription


def test_encrypted_json_roundtrip_and_legacy_rows(hospital):
    from django.db import connection

    p = Patient.objects.create(hospital=hospital, first_name="E", mobile="9000000800")
    meds = [{"name": "Amlodipine 5mg", "frequency": "OD"}]
    rx = Prescription.objects.create(hospital=hospital, patient=p, diagnosis="H", medications=meds)
    assert Prescription.objects.get(pk=rx.pk).medications == meds
    with connection.cursor() as c:
        c.execute(f"SELECT medications FROM {Prescription._meta.db_table} WHERE id=%s", [rx.pk])
        stored = c.fetchone()[0]
    assert "Amlodipine" not in stored  # still encrypted at rest
    # Row in the old (buggy) on-disk format: JSON-quoted ciphertext of the Python repr.
    legacy = json.dumps(encrypt_value(str(meds)))
    with connection.cursor() as c:
        c.execute(f"UPDATE {Prescription._meta.db_table} SET medications=%s WHERE id=%s", [legacy, rx.pk])
    assert Prescription.objects.get(pk=rx.pk).medications == meds
