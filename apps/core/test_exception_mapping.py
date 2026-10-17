"""Input mistakes that surface below the serializer come back as 4xx, not 500."""
from decimal import Decimal, InvalidOperation

import pytest
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError

from apps.core.exceptions import api_exception_handler
from apps.core.models import Department


def _handle(exc):
    return api_exception_handler(exc, {})


@pytest.mark.django_db
def test_deleting_a_record_still_in_use_is_a_conflict(hospital):
    dept = Department.objects.create(hospital=hospital, name="Cardiology")
    resp = _handle(ProtectedError("in use", {dept}))
    assert resp.status_code == 409
    assert resp.data["code"] == "in_use"
    assert "departments" in resp.data["detail"]


def test_non_numeric_id_is_a_bad_request():
    with pytest.raises(ValueError) as err:
        Department._meta.get_field("id").get_prep_value("Example vendor")
    resp = _handle(err.value)
    assert resp.status_code == 400
    assert "'Example vendor' is not a valid id" in resp.data["detail"]


def test_other_value_errors_are_not_swallowed():
    assert _handle(ValueError("something else broke")) is None


def test_bad_number_and_model_validation_are_bad_requests():
    with pytest.raises(InvalidOperation) as err:
        Decimal("12abc")
    assert _handle(err.value).status_code == 400
    assert _handle(ValidationError({"date": ["Enter a valid date."]})).data == {"date": ["Enter a valid date."]}
    assert _handle(ValidationError("Not allowed.")).data == {"detail": "Not allowed."}
