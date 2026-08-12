import pytest

from apps.enquiries.models import Enquiry
from apps.enquiries.services import assign_enquiry, find_duplicates


@pytest.mark.django_db
def test_find_duplicates_matches_on_mobile(hospital):
    first = Enquiry.objects.create(hospital=hospital, name="Asha Patil", mobile="9876543210", source=Enquiry.Source.WEBSITE)

    duplicates = find_duplicates(hospital, mobile="9876543210")
    assert list(duplicates) == [first]


@pytest.mark.django_db
def test_second_enquiry_from_same_number_is_flagged_as_duplicate(hospital):
    first = Enquiry.objects.create(hospital=hospital, name="Asha Patil", mobile="9876543210", source=Enquiry.Source.WEBSITE)
    second = Enquiry.objects.create(hospital=hospital, name="Asha P.", mobile="9876543210", source=Enquiry.Source.IVR)

    second.refresh_from_db()
    assert second.duplicate_of_id == first.id


@pytest.mark.django_db
def test_assign_enquiry_picks_least_loaded_department_user(hospital, department, user):
    enquiry = Enquiry.objects.create(hospital=hospital, name="Ravi Kumar", mobile="9123456780", source=Enquiry.Source.WALK_IN, department=department)
    owner = assign_enquiry(enquiry)
    assert owner == user
    enquiry.refresh_from_db()
    assert enquiry.assigned_to_id == user.id
