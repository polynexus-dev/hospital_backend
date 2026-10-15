"""Staff / dependent rate category: a patient who is a serving employee, or
an active dependent of one, is billed in the "staff" patient category
(ServiceTariff.category_rates["staff"]; tariffs without a staff rate fall
back to the base rate). An employee stops counting as serving once their
exit is completed and the last working day has passed."""
from django.db.models import Q
from django.utils import timezone

STAFF_CATEGORY = "staff"


def _serving(employee, today):
    return not employee.exit_requests.filter(status="completed", last_working_day__lt=today).exists()


def staff_link(patient):
    """{"kind": "staff"|"dependent", employee, employee_code, employee_name, relationship} or None."""
    if patient is None or not patient.pk:
        return None
    from .models import Employee, EmployeeDependent

    today = timezone.localdate()
    emp = Employee.objects.filter(patient=patient).select_related("user").first()
    if emp is not None and _serving(emp, today):
        return {"kind": "staff", "employee": emp.pk, "employee_code": emp.employee_code, "employee_name": _name(emp), "relationship": ""}
    deps = (
        EmployeeDependent.objects.filter(patient=patient, is_active=True)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
        .select_related("employee__user")
    )
    for dep in deps:
        if _serving(dep.employee, today):
            return {"kind": "dependent", "employee": dep.employee_id, "employee_code": dep.employee.employee_code,
                    "employee_name": _name(dep.employee), "relationship": dep.get_relationship_display()}
    return None


def _name(emp):
    return (emp.user.get_full_name() if emp.user else "") or emp.designation


def default_category(patient, category):
    """The category a new bill should get: staff for staff-linked patients
    when nothing more specific (private / insurance / corporate) was chosen."""
    if category in ("", "general") and staff_link(patient):
        return STAFF_CATEGORY
    return category
