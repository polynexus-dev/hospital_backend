"""reencrypt_fields moves data from one set of keys to another (e.g. off the
public dev keys) without losing a record or breaking phone search."""
import base64
import os

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import override_settings

from apps.core.encryption import blind_index, normalize_phone, reset_fernet_cache
from apps.patients.models import Patient

NEW_GCM = base64.urlsafe_b64encode(os.urandom(32)).decode()
NEW_HASH = "rotated-hash-key-for-test"


def stored(column, pk):
    with connection.cursor() as cur:
        cur.execute(f"SELECT {column} FROM patients_patient WHERE id = %s", [pk])
        return cur.fetchone()[0]


@pytest.fixture(autouse=True)
def _fresh_keys():
    reset_fernet_cache()
    yield
    reset_fernet_cache()


def test_rotation_rewrites_values_and_hashes_under_the_new_keys(hospital, settings):
    old_gcm = settings.FIELD_ENCRYPTION_KEY_V2
    p = Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Patil", mobile="+919000012345")
    old_token = stored("mobile", p.pk)

    with override_settings(FIELD_ENCRYPTION_KEYS_V2=[NEW_GCM, old_gcm], FIELD_HASH_KEY=NEW_HASH):
        reset_fernet_cache()
        call_command("reencrypt_fields")

    with override_settings(FIELD_ENCRYPTION_KEYS_V2=[NEW_GCM], FIELD_HASH_KEY=NEW_HASH):  # old key gone
        reset_fernet_cache()
        p = Patient.objects.get(pk=p.pk)
        assert (p.first_name, p.mobile) == ("Asha", "+919000012345")
        assert stored("mobile", p.pk) != old_token
        assert p.mobile_hash == blind_index(normalize_phone("+919000012345"))  # phone search still finds her


def test_dry_run_changes_nothing(hospital, settings):
    p = Patient.objects.create(hospital=hospital, first_name="Ravi", last_name="Kale", mobile="+919000054321")
    before = stored("mobile", p.pk)
    with override_settings(FIELD_ENCRYPTION_KEYS_V2=[NEW_GCM, settings.FIELD_ENCRYPTION_KEY_V2]):
        reset_fernet_cache()
        call_command("reencrypt_fields", "--dry-run")
    assert stored("mobile", p.pk) == before


def test_values_no_key_can_read_are_skipped_not_destroyed(hospital, settings):
    p = Patient.objects.create(hospital=hospital, first_name="Meera", last_name="Joshi", mobile="+919000099999")
    before = stored("mobile", p.pk)
    with override_settings(FIELD_ENCRYPTION_KEYS_V2=[NEW_GCM]):  # the key it was written with is missing
        reset_fernet_cache()
        call_command("reencrypt_fields")
    assert stored("mobile", p.pk) == before  # left as is, recoverable once the old key is supplied
