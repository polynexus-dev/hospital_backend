import pytest

from apps.clinical.models import AssessmentTemplate
from apps.clinical.specialty_templates import SPECIALTY_TEMPLATES, install_specialty_library
from apps.clinical.serializers import AssessmentTemplateSerializer
from apps.patients.models import Patient


@pytest.mark.django_db
def test_every_library_template_is_valid_for_the_template_builder():
    for name, category, setting, fields in SPECIALTY_TEMPLATES:
        ser = AssessmentTemplateSerializer(data={"name": name, "category": category, "setting": setting, "fields": fields})
        ser.is_valid()
        assert not {k: v for k, v in ser.errors.items() if k != "hospital"}, (name, ser.errors)


@pytest.mark.django_db
def test_install_adds_only_missing_templates_and_keeps_hospital_edits(hospital):
    AssessmentTemplate.objects.filter(hospital=hospital).delete()
    assert len(install_specialty_library(hospital)) == len(SPECIALTY_TEMPLATES)
    cardio = AssessmentTemplate.objects.get(hospital=hospital, name="Cardiology consultation")
    cardio.fields = cardio.fields[:2]
    cardio.save()
    assert install_specialty_library(hospital) == []
    cardio.refresh_from_db()
    assert len(cardio.fields) == 2


@pytest.mark.django_db
def test_install_library_endpoint_and_manual_template_creation(auth_client, hospital):
    AssessmentTemplate.objects.filter(hospital=hospital, category="dental").delete()
    res = auth_client.post("/api/v1/clinical/assessment-templates/install-library/")
    assert res.status_code == 200 and "Dental consultation" in res.data["added"]

    # Hand-built template: select without options and duplicate keys are rejected.
    bad = {"name": "My template", "category": "general", "setting": "opd", "fields": [{"key": "a", "label": "A", "type": "select"}]}
    assert auth_client.post("/api/v1/clinical/assessment-templates/", bad, format="json").status_code == 400
    dup = {**bad, "fields": [{"key": "a", "label": "A", "type": "text"}, {"key": "a", "label": "B", "type": "text"}]}
    assert auth_client.post("/api/v1/clinical/assessment-templates/", dup, format="json").status_code == 400
    ok = {**bad, "fields": [{"key": "grade", "label": "Grade", "type": "select", "options": ["I", "II"], "required": True}]}
    assert auth_client.post("/api/v1/clinical/assessment-templates/", ok, format="json").status_code == 201


@pytest.mark.django_db
def test_answers_must_fit_the_template(auth_client, hospital):
    install_specialty_library(hospital)
    template = AssessmentTemplate.objects.get(hospital=hospital, name="Cardiology consultation")
    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9876500021")
    base = {"patient": patient.pk, "template": template.pk}

    res = auth_client.post("/api/v1/clinical/assessments/", {**base, "data": {"nyha_class": "II"}}, format="json")
    assert res.status_code == 400 and "Presenting symptoms" in str(res.data)  # required
    res = auth_client.post("/api/v1/clinical/assessments/", {**base, "data": {"presenting_symptoms": ["Chest pain"], "nyha_class": "V", "echo_lvef": "abc"}}, format="json")
    assert res.status_code == 400 and "NYHA" in str(res.data) and "LVEF" in str(res.data)
    res = auth_client.post("/api/v1/clinical/assessments/", {**base, "data": {"presenting_symptoms": ["Chest pain", "Syncope"], "nyha_class": "II", "echo_lvef": 45}}, format="json")
    assert res.status_code == 201 and res.data["category"] == "cardiology"
