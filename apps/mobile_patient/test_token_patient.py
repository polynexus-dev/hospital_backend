import pytest


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/v1/mobile/me/", "/api/v1/mobile/bills/", "/api/v1/mobile/me/family/"])
def test_staff_login_without_a_patient_token_is_refused_not_crashed(auth_client, path):
    """A staff session/JWT carries no patient_id: every patient endpoint answers 403 instead of a 500."""
    response = auth_client.get(path)
    assert response.status_code == 403
    assert "No patient associated with token" in response.json()["detail"]


@pytest.mark.django_db
def test_offline_sync_rejects_malformed_rows(auth_client):
    response = auth_client.post("/api/v1/patients/offline-sync/", {"patients": "not-a-list"}, format="json")
    assert response.status_code == 400
    response = auth_client.post("/api/v1/patients/offline-sync/", {"patients": ["oops"]}, format="json")
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "error"
