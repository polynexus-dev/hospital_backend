from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import Group, PermissionsMixin
from django.db import models

from apps.core.fields import EncryptedCharField
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
        # `manage.py createsuperuser` is how this platform's operator
        # account gets made, and an operator who can't see the SaaS
        # console is not what anyone means by "superuser" here.
        # apps.core.permissions.IsSaaSAdmin already treats is_superuser as
        # sufficient on its own, so this changes no permission decision —
        # it makes the *flag* match, which is what the product UI reads to
        # decide whether to show the SaaS console at all. setdefault, not
        # a hard assignment: `create_superuser(..., is_saas_admin=False)`
        # still gets you a pure-infrastructure account with no operator
        # persona in the UI.
        extra_fields.setdefault("is_saas_admin", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class Role(TimeStampedModel):
    """A named permission set, scoped to a hospital and optionally to a department."""

    class Template(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Administrator"
        DOCTOR = "doctor", "OPD Doctor"
        FRONT_DESK = "front_desk", "Front Desk"
        TELEPHONY_OPERATOR = "telephony_operator", "Telephony Operator"
        CRM_SUPER_ADMIN = "crm_super_admin", "CRM Super Admin"
        CRM_MANAGER = "crm_manager", "CRM Manager"
        CRM_EXECUTIVE = "crm_executive", "CRM Executive"
        CALL_CENTRE_EXECUTIVE = "call_centre_executive", "Call Centre Executive"
        MARKETING_MANAGER = "marketing_manager", "Marketing Manager"
        CORPORATE_RM = "corporate_rm", "Corporate Relationship Manager"
        CRM_AUDITOR = "crm_auditor", "CRM Auditor"
        RECEPTIONIST = "receptionist", "Receptionist / ERP Front Desk"
        HOSPITAL_ADMINISTRATOR = "hospital_administrator", "Hospital Administrator"
        NURSE = "nurse", "Nurse"
        LAB_TECHNICIAN = "lab_technician", "Lab Technician"
        LAB_MANAGER = "lab_manager", "Lab Pathologist / Manager"
        RADIOLOGY_TECHNICIAN = "radiology_technician", "Radiology Technician"
        RADIOLOGIST = "radiologist", "Radiologist"
        PHARMACIST = "pharmacist", "Pharmacist"
        OT_MANAGER = "ot_manager", "OT Manager"
        SURGEON = "surgeon", "Surgeon"
        ANAESTHETIST = "anaesthetist", "Anaesthetist"
        ICU_STAFF = "icu_staff", "ICU Staff"
        BLOOD_BANK_TECHNICIAN = "blood_bank_technician", "Blood Bank Technician"
        FINANCE_MANAGER = "finance_manager", "Finance Manager"
        HR_MANAGER = "hr_manager", "HR Manager"
        HIM_OFFICER = "him_officer", "HIM Officer"
        HOSPITAL_AUDITOR = "hospital_auditor", "Hospital Auditor"
        BILLING_EXECUTIVE = "billing_executive", "Billing Executive"
        BILLING_MANAGER = "billing_manager", "Billing Manager"
        INSURANCE_TPA_EXECUTIVE = "insurance_tpa_executive", "Insurance / TPA Executive"
        INVENTORY_MANAGER = "inventory_manager", "Inventory Manager"
        PURCHASE_MANAGER = "purchase_manager", "Purchase Manager"

    class DataScope(models.TextChoices):
        ALL = "all", "All hospital records"
        ASSIGNED_ONLY = "assigned_only", "Assigned records only"

    class Domain(models.TextChoices):
        CRM = "crm", "CRM"
        ERP = "erp", "ERP"
        BOTH = "both", "Both"

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="roles")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="roles")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    template = models.CharField(
        max_length=32, choices=Template.choices, blank=True,
        help_text="Permission set applied on creation; features added later are granted after each migrate (removals are kept).",
    )
    # Which "app.model"s the template has been applied for — see role_sync.
    template_synced_models = models.JSONField(default=list, blank=True, editable=False)
    data_scope = models.CharField(
        max_length=16, choices=DataScope.choices, default=DataScope.ALL,
        help_text="assigned_only narrows any ViewSet declaring assignment_scope_field to records assigned to the requesting user.",
    )
    domain = models.CharField(
        max_length=8, choices=Domain.choices, default=Domain.BOTH,
        help_text="Drives which top-level nav (CRM/ERP/both) the frontend shows for users with this role.",
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
            from .role_sync import sync_role

            sync_role(self)  # applies the template and records which models it covered

    @property
    def permissions(self):
        return self.group.permissions


class User(AbstractBaseUser, PermissionsMixin):
    class SaaSRole(models.TextChoices):
        OWNER = "saas_owner", "SaaS Owner"
        PLATFORM_ADMIN = "platform_admin", "Platform Admin"
        SUPPORT_L1 = "support_l1", "Support L1"
        SUPPORT_L2 = "support_l2", "Support L2"
        SUPPORT_LEAD = "support_lead", "Support Lead"
        BILLING = "billing", "Billing / Finance"
        CUSTOMER_SUCCESS = "customer_success", "Customer Success"
        SECURITY_AUDITOR = "security_auditor", "Security / Compliance Auditor"
        DEVOPS = "devops", "DevOps / Engineering"

    SAAS_ROLE_CAPABILITIES = {
        SaaSRole.OWNER: {"platform", "tenant_manage", "billing_manage", "support_manage", "hospital_access", "security_review", "saas_user_manage"},
        SaaSRole.PLATFORM_ADMIN: {"platform", "tenant_manage", "support_manage", "hospital_access"},
        SaaSRole.SUPPORT_L1: {"platform", "support_manage"},
        SaaSRole.SUPPORT_L2: {"platform", "support_manage", "hospital_access"},
        SaaSRole.SUPPORT_LEAD: {"platform", "support_manage", "hospital_access"},
        SaaSRole.BILLING: {"platform", "billing_manage"},
        SaaSRole.CUSTOMER_SUCCESS: {"platform", "tenant_view", "support_manage"},
        SaaSRole.SECURITY_AUDITOR: {"platform", "security_review"},
        SaaSRole.DEVOPS: {"platform"},
    }
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
    is_saas_admin = models.BooleanField(
        default=False,
        help_text="Master SaaS Admin / platform owner — manages tenants, subscriptions, billing, and support tickets across every hospital.",
    )
    saas_role = models.CharField(
        max_length=32, choices=SaaSRole.choices, blank=True,
        help_text="Platform-company role. Assigned and changed only by the SaaS Owner.",
    )
    is_2fa_enabled = models.BooleanField(default=False)
    totp_secret = EncryptedCharField(max_length=64, blank=True, editable=False)
    allowed_ip_ranges = models.JSONField(default=list, blank=True, help_text="CIDR ranges this user may log in from; empty = unrestricted.")

    date_joined = models.DateTimeField(auto_now_add=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)

    # NABH DOM.4.a/c — see apps.governance.services for the policy logic.
    password_changed_at = models.DateTimeField(null=True, blank=True, editable=False)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0, editable=False)
    locked_until = models.DateTimeField(null=True, blank=True, editable=False)
    is_blocked = models.BooleanField(default=False, help_text="Administratively blocked — cannot sign in until unblocked.")
    blocked_reason = models.CharField(max_length=255, blank=True)
    signature_image = models.ImageField(upload_to="signatures/", blank=True, help_text="Digital signature stamped on signed clinical documents (COP.1.e).")
    registration_number = models.CharField(max_length=64, blank=True, help_text="Medical/nursing council registration number, printed with the signature.")

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        if self.is_saas_admin:
            self.is_staff = True
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "is_staff" not in update_fields:
                kwargs["update_fields"] = list(update_fields) + ["is_staff"]
        super().save(*args, **kwargs)

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

    def get_short_name(self):
        return self.first_name or self.email

    @property
    def requires_mfa(self) -> bool:
        if self.is_staff:
            return True
        if self.hospital_id:
            from apps.governance.models import SecurityPolicy

            if SecurityPolicy.for_hospital(self.hospital_id).enforce_mfa_for_all:
                return True
        return bool(self.role_id) and self.role.template in (Role.Template.OWNER, Role.Template.ADMIN)

    @property
    def is_locked_out(self) -> bool:
        from django.utils import timezone

        return bool(self.locked_until and self.locked_until > timezone.now())

    @property
    def can_cross_tenant(self) -> bool:
        """True only for genuine platform-ops accounts — mirrors
        apps.core.permissions.IsSaaSAdmin exactly, and is the single check
        every cross-hospital mechanism (X-Hospital-Id header, switch-hospital,
        available_hospitals, UserViewSet/RoleViewSet "all hospitals" queries)
        must use.

        Deliberately narrower than is_staff: is_staff is also granted to
        ordinary hospital Owner/Admin accounts (see
        apps.saas_admin.tenant_service, which sets is_staff=True on every
        new tenant's Owner so they can reach their own hospital's Admin
        Console — AuditLogViewSet, IntegrationHealthView). Gating
        cross-hospital reads/writes on bare is_staff let any hospital's
        Owner read and write every *other* hospital's data on the platform
        by sending an X-Hospital-Id header — verified empirically, not
        theoretical."""
        if self.is_saas_admin:
            return self.has_saas_capability("hospital_access")
        return bool(self.is_superuser and not self.hospital_id)

    def has_saas_capability(self, capability: str) -> bool:
        if self.is_superuser and not self.hospital_id:
            return True
        capabilities = self.SAAS_ROLE_CAPABILITIES.get(self.saas_role, set())
        if capability == "tenant_view":
            return "tenant_view" in capabilities or "tenant_manage" in capabilities
        if capability == "analytics_view":
            return bool({"tenant_manage", "tenant_view", "billing_manage"} & capabilities)
        return capability in capabilities

    @property
    def is_saas_owner(self) -> bool:
        return self.is_superuser or self.saas_role == self.SaaSRole.OWNER

    def can_manage_saas_role(self, role: str) -> bool:
        """Delegated platform-account administration. A caller can only
        create or change identities below their own tier; nobody delegates
        billing, security, DevOps, manager, or owner power accidentally."""
        if self.is_saas_owner:
            return role in self.SaaSRole.values
        if self.saas_role == self.SaaSRole.PLATFORM_ADMIN:
            return role in {self.SaaSRole.SUPPORT_LEAD, self.SaaSRole.SUPPORT_L1, self.SaaSRole.SUPPORT_L2}
        if self.saas_role == self.SaaSRole.SUPPORT_LEAD:
            return role in {self.SaaSRole.SUPPORT_L1, self.SaaSRole.SUPPORT_L2}
        return False

    def is_login_ip_allowed(self, ip_address: str) -> bool:
        if not self.allowed_ip_ranges or not ip_address:
            return True
        import ipaddress
        try:
            addr = ipaddress.ip_address(ip_address)
        except ValueError:
            return False
        for cidr in self.allowed_ip_ranges:
            try:
                if addr in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
        return False


def assign_role(user: User, role: Role | None) -> None:
    if user.role_id and user.role.group_id:
        user.groups.remove(user.role.group)
    if role is not None:
        user.groups.add(role.group)
    user.role = role
    user.save(update_fields=["role"])


from .models_sso import SSOIdentity, SSOLoginAttempt, SSOProvider  # noqa: E402,F401
