from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import Group, PermissionsMixin
from django.db import models

from apps.core.models import Department, Hospital, TimeStampedModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class Role(TimeStampedModel):
    """A named permission set, scoped to a hospital and optionally to a
    department (e.g. "Front Desk Operator", "PRO", "Owner"). Backed by a
    Django Group so DRF's permission checks (apps.core.permissions.
    RoleBasedModelPermissions, has_perm()) work without a parallel
    permission-checking system."""

    class Template(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Administrator"
        DOCTOR = "doctor", "OPD Doctor"
        FRONT_DESK = "front_desk", "Front Desk"
        TELEPHONY_OPERATOR = "telephony_operator", "Telephony Operator"

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="roles")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="roles")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    template = models.CharField(
        max_length=32, choices=Template.choices, blank=True,
        help_text="Applies a starter permission set on creation only — see apps.accounts.permission_templates. Leave blank for a hand-built role with no default permissions.",
    )
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="role", editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hospital", "name"], name="unique_role_name_per_hospital"),
        ]

    def __str__(self):
        return f"{self.name} ({self.hospital.name})"

    def save(self, *args, **kwargs):
        is_new = not self.group_id
        if is_new:
            self.group = Group.objects.create(name=f"{self.hospital_id}:{self.name}")
        super().save(*args, **kwargs)
        if is_new and self.template:
            from .permission_templates import apply_permission_template
            apply_permission_template(self.group, self.template)

    @property
    def permissions(self):
        return self.group.permissions


class User(AbstractBaseUser, PermissionsMixin):
    class PreferredLanguage(models.TextChoices):
        MARATHI = "mr", "Marathi"
        HINDI = "hi", "Hindi"
        ENGLISH = "en", "English"

    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, null=True, blank=True, related_name="users")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="users")
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, related_name="users")

    preferred_language = models.CharField(max_length=8, choices=PreferredLanguage.choices, default=PreferredLanguage.ENGLISH)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_2fa_enabled = models.BooleanField(default=False)
    allowed_ip_ranges = models.JSONField(default=list, blank=True, help_text="CIDR ranges this user may log in from; empty = unrestricted.")

    date_joined = models.DateTimeField(auto_now_add=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return self.email

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

    def get_short_name(self):
        return self.first_name or self.email


def assign_role(user: User, role: Role | None) -> None:
    """Swaps a user's role, keeping Django group membership (and therefore
    permissions) in sync with the assignment."""
    if user.role_id and user.role.group_id:
        user.groups.remove(user.role.group)
    if role is not None:
        user.groups.add(role.group)
    user.role = role
    user.save(update_fields=["role"])
