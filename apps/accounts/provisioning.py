"""Creating staff logins: the checks every new login must pass (subscription
and licence user limits, who may hand out which role), shared by
UserViewSet.create and the doctor profile's "Give login access"."""
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import Role, User, assign_role
from .permission_catalog import role_assignment_error


def check_staff_capacity(hospital):
    """Raises ValidationError when the hospital can't have another active login."""
    from apps.licensing.service import CapacityExceeded, check_capacity
    from apps.saas_admin.models import TenantSubscription

    active = User.objects.filter(hospital=hospital, is_active=True).count()
    sub = TenantSubscription.objects.filter(hospital=hospital, status=TenantSubscription.Status.ACTIVE).first()
    if sub and sub.max_staff_users > 0 and active >= sub.max_staff_users:
        raise ValidationError({"detail": f"Hospital has reached its subscription staff limit of {sub.max_staff_users} users."})
    try:
        check_capacity("users", active)  # on-premise licence: max_active_users
    except CapacityExceeded as exc:
        raise ValidationError({"detail": str(exc)})


def doctor_role(hospital) -> Role:
    """The hospital's role for consultant logins (created from the doctor template if missing)."""
    roles = Role.objects.filter(hospital=hospital, template=Role.Template.DOCTOR)
    return roles.filter(name="Consultant Doctor").first() or roles.order_by("name").first() or Role.objects.create(
        hospital=hospital, name="Consultant Doctor", template=Role.Template.DOCTOR
    )


def create_login(*, actor, hospital, email, first_name="", last_name="", role=None, department=None) -> User:
    """A new active login with no usable password yet (issue a temporary one next)."""
    email = (email or "").strip().lower()
    if not email:
        raise ValidationError({"email": ["An email address is needed to sign in."]})
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError({"email": ["A login with this email already exists."]})
    error = role_assignment_error(actor, role)
    if error:
        raise PermissionDenied(error)
    check_staff_capacity(hospital)
    user = User(email=email, first_name=first_name, last_name=last_name, hospital=hospital, department=department, is_active=True)
    user.set_unusable_password()
    user.save()
    assign_role(user, role)
    return user
