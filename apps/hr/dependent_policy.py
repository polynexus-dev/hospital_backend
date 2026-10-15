"""Dependent linking policy (MedNet M2.22 HR "MRN linking / unlinking
policy"): how many dependents an employee may link (by employment type,
marital status, gender) and, per relation, an age limit, unmarried only and
a maximum count. No rules set = no limits (the previous behaviour)."""
from datetime import date

from django.utils import timezone
from rest_framework import serializers


def _age(dob, today):
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _birthday(dob, years):
    try:
        return dob.replace(year=dob.year + years)
    except ValueError:  # 29 February
        return date(dob.year + years, 3, 1)


def allowance_for(emp):
    """The most specific matching DependentAllowance, or None (no limit)."""
    from .models import DependentAllowance

    rules = [r for r in DependentAllowance.objects.filter(hospital_id=emp.hospital_id) if r.matches(emp)]
    return max(rules, key=lambda r: r.specificity(), default=None)


def check_dependent(emp, patient, relationship, is_married, exclude_pk=None):
    """Raises ValidationError if linking breaks the policy. Returns the date
    cover should end at (the relation's age limit) or None."""
    from .models import DependentRelationRule, EmployeeDependent

    today = timezone.localdate()
    others = EmployeeDependent.objects.filter(employee=emp, is_active=True).exclude(pk=exclude_pk)
    allowance = allowance_for(emp)
    if allowance is not None and others.count() + 1 > allowance.max_dependents:
        raise serializers.ValidationError({"employee": f"{emp.employee_code} may link at most {allowance.max_dependents} dependent(s) "
                                                       f"under the dependent policy; {others.count()} already linked."})
    rule = DependentRelationRule.objects.filter(hospital_id=emp.hospital_id, relationship=relationship).first()
    if rule is None:
        return None
    label = dict(EmployeeDependent.Relationship.choices).get(relationship, relationship)
    if rule.unmarried_only and is_married:
        raise serializers.ValidationError({"is_married": f"A {label.lower()} is covered only while unmarried."})
    if rule.max_count is not None and others.filter(relationship=relationship).count() + 1 > rule.max_count:
        raise serializers.ValidationError({"relationship": f"At most {rule.max_count} {label.lower()}(s) can be linked."})
    if rule.max_age is None:
        return None
    dob = patient.date_of_birth
    if dob is None:
        raise serializers.ValidationError({"patient": f"A {label.lower()} is covered up to age {rule.max_age}: record the date of birth on the patient first."})
    if _age(dob, today) >= rule.max_age:
        raise serializers.ValidationError({"patient": f"A {label.lower()} is covered only until age {rule.max_age}."})
    return _birthday(dob, rule.max_age)
