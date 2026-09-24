"""
IT-governance controls from the NABH HIS/EMR standard's DOM/DAC chapters
(and AAC.7.b): per-hospital security policy, the security-event log,
configurable audit-capture/retention rules, backups, the in-app help
centre, accreditations shown on the login page, and vendor release notes.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import Hospital, TenantScopedModel, TimeStampedModel


class SecurityPolicy(TimeStampedModel):
    """One row per hospital (DOM.4.a/b/c/e). Defaults are the values NABH's
    own test cases use as examples (90-day renewal, lock after inactivity)
    so a new tenant is compliant before anyone opens the settings screen."""

    hospital = models.OneToOneField(Hospital, on_delete=models.CASCADE, related_name="security_policy")

    password_min_length = models.PositiveSmallIntegerField(default=10)
    password_require_uppercase = models.BooleanField(default=True)
    password_require_lowercase = models.BooleanField(default=True)
    password_require_digit = models.BooleanField(default=True)
    password_require_symbol = models.BooleanField(default=True)
    password_expiry_days = models.PositiveSmallIntegerField(default=90, help_text="0 = never expires.")
    password_history_count = models.PositiveSmallIntegerField(default=5, help_text="Reject reuse of the last N passwords.")

    lockout_threshold = models.PositiveSmallIntegerField(default=5, help_text="Failed logins before the account locks. 0 = never lock.")
    lockout_minutes = models.PositiveSmallIntegerField(default=30)

    idle_lock_enabled = models.BooleanField(default=True)
    idle_lock_minutes = models.PositiveSmallIntegerField(default=10)

    enforce_mfa_for_all = models.BooleanField(default=False, help_text="Require MFA for every user, not only owner/admin roles.")

    def __str__(self):
        return f"Security policy — {self.hospital.name}"

    @classmethod
    def for_hospital(cls, hospital_or_id):
        if hospital_or_id is None:
            return cls()  # unsaved defaults — platform users with no hospital
        hospital_id = getattr(hospital_or_id, "pk", hospital_or_id)
        policy, _ = cls.objects.get_or_create(hospital_id=hospital_id)
        return policy


class PasswordHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="password_history")
    password_hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class SecurityEvent(models.Model):
    """Critical security incidents & events (DOM.3.a). Append-only, like
    apps.core.models.AuditLog — kept separate from it because an auditor
    reviewing *security* events (failed logins, lockouts, denied access)
    shouldn't have to wade through every ordinary record edit."""

    class EventType(models.TextChoices):
        LOGIN_SUCCESS = "login_success", "Login success"
        LOGIN_FAILED = "login_failed", "Login failed"
        ACCOUNT_LOCKED = "account_locked", "Account locked"
        ACCOUNT_UNLOCKED = "account_unlocked", "Account unlocked"
        USER_BLOCKED = "user_blocked", "User blocked"
        USER_UNBLOCKED = "user_unblocked", "User unblocked"
        LOCKED_LOGIN_ATTEMPT = "locked_login_attempt", "Login attempt on locked account"
        PASSWORD_CHANGED = "password_changed", "Password changed"
        PASSWORD_EXPIRED = "password_expired", "Password expired"
        MFA_FAILED = "mfa_failed", "MFA verification failed"
        ACCESS_DENIED = "access_denied", "Unauthorized access attempt"
        IP_BLOCKED = "ip_blocked", "Login from disallowed network"
        SCREEN_UNLOCK_FAILED = "screen_unlock_failed", "Screen unlock failed"
        DATA_EXPORT = "data_export", "Bulk data export"
        BACKUP = "backup", "Backup"
        ROLLBACK = "rollback", "Change rolled back"
        POLICY_CHANGED = "policy_changed", "Security policy changed"

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    hospital = models.ForeignKey(Hospital, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_events")
    username_attempted = models.CharField(max_length=254, blank=True)
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.INFO)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    path = models.CharField(max_length=512, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["hospital", "event_type", "created_at"])]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("SecurityEvent records are immutable.")
        super().save(*args, **kwargs)


class AuditRule(TenantScopedModel):
    """DAC.2.c — which actions get captured, for which models, and how long
    they're retained. `model_name` "*" matches every model. The per-request
    AuditMiddleware log is always on (that's the baseline trail); these
    rules decide which of the finer-grained entries are kept and for how
    long, and can switch on READ capture for sensitive models."""

    name = models.CharField(max_length=120)
    model_name = models.CharField(max_length=100, default="*")
    actions = models.JSONField(default=list, help_text='Subset of ["create","update","delete","read","request","export"]; empty = all.')
    capture_reads = models.BooleanField(default=False)
    retention_days = models.PositiveIntegerField(default=2555, help_text="≈7 years by default.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RetentionPolicy(TimeStampedModel):
    hospital = models.OneToOneField(Hospital, on_delete=models.CASCADE, related_name="retention_policy")
    backup_retention_days = models.PositiveIntegerField(default=1825, help_text="Backups older than this are deleted (default 5 years).")
    auto_backup_enabled = models.BooleanField(default=True)
    auto_backup_frequency_hours = models.PositiveSmallIntegerField(default=24)
    archive_audit_logs_after_days = models.PositiveIntegerField(default=2555)

    @classmethod
    def for_hospital(cls, hospital):
        policy, _ = cls.objects.get_or_create(hospital=hospital)
        return policy


class BackupRecord(TenantScopedModel):
    class Kind(models.TextChoices):
        MANUAL = "manual", "Manual"
        SCHEDULED = "scheduled", "Scheduled"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired & purged"
        RESTORED = "restored", "Restored"

    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.MANUAL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    file_path = models.CharField(max_length=500, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    record_count = models.PositiveIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]


class HelpArticle(TimeStampedModel):
    """DOM.1.b. hospital=None rows are platform-wide (shipped with the
    product); a hospital can add its own SOP-style articles on top."""

    class Category(models.TextChoices):
        GUIDE = "guide", "User guide"
        FAQ = "faq", "FAQ"
        TUTORIAL = "tutorial", "Tutorial"
        TROUBLESHOOTING = "troubleshooting", "Troubleshooting"
        POLICY = "policy", "Policy / SOP"

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, null=True, blank=True, related_name="help_articles")
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.GUIDE)
    module = models.CharField(max_length=40, blank=True, help_text="e.g. opd, laboratory, billing")
    title = models.CharField(max_length=200)
    body = models.TextField()
    video_url = models.URLField(blank=True)
    tags = models.CharField(max_length=300, blank=True)
    order = models.PositiveSmallIntegerField(default=100)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "order", "title"]

    def __str__(self):
        return self.title


class Accreditation(TenantScopedModel):
    """AAC.7.b — shown on the (public) login page."""

    name = models.CharField(max_length=200, help_text="e.g. NABH Full Accreditation")
    issuing_body = models.CharField(max_length=200, default="NABH")
    certificate_number = models.CharField(max_length=100, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    logo = models.ImageField(upload_to="accreditations/", blank=True)
    show_on_login = models.BooleanField(default=True)

    class Meta:
        ordering = ["-issued_on"]

    def __str__(self):
        return self.name


class ReleaseNote(TimeStampedModel):
    """DOM.2.a — the vendor's patch/update feed, platform-wide."""

    class Kind(models.TextChoices):
        FEATURE = "feature", "Feature"
        BUGFIX = "bugfix", "Bug fix"
        SECURITY = "security", "Security patch"

    version = models.CharField(max_length=40)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.FEATURE)
    released_on = models.DateField(default=timezone.localdate)
    summary = models.CharField(max_length=300)
    details = models.TextField(blank=True)

    class Meta:
        ordering = ["-released_on", "-id"]

    def __str__(self):
        return f"{self.version} — {self.summary}"
