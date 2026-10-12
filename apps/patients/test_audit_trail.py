import json

import pytest

from apps.core.models import AuditLog
from apps.patients.models import Patient


# NABH AAC.1.a (register and modify a patient)
@pytest.mark.django_db
def test_patient_changes_are_audited_without_leaking_encrypted_values(auth_client, user, hospital):
    res = auth_client.post("/api/v1/patients/", {"first_name": "Asha", "last_name": "Rao", "mobile": "9876500011", "confirm_not_duplicate": True}, format="json")
    assert res.status_code == 201, res.data
    patient = Patient.objects.get(pk=res.data["id"])

    created = AuditLog.objects.filter(model_name="Patient", object_id=str(patient.pk), action="create").first()
    assert created and created.actor_id == user.pk
    # Names are encrypted at rest too, so they are recorded as changed, never by value.
    assert created.changes["first_name"] == "[redacted]" and created.changes["mobile"] == "[redacted]"
    assert created.changes["gender"] == ""

    auth_client.patch(f"/api/v1/patients/{patient.pk}/", {"last_name": "Rao-Iyer", "mobile": "9876500022"}, format="json")
    updated = AuditLog.objects.filter(model_name="Patient", object_id=str(patient.pk), action="update").latest("id")
    assert updated.changes["last_name"] == {"changed": True}
    assert updated.changes["mobile"] == {"changed": True}
    assert updated.ip_address and updated.path.endswith(f"/patients/{patient.pk}/")

    everything = json.dumps(list(AuditLog.objects.values("changes", "object_repr")))
    assert "9876500011" not in everything and "9876500022" not in everything
    assert "Asha" not in everything and "Rao-Iyer" not in everything
