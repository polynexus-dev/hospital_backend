"""Idempotently provision the baseline SaaS-company accounts for a VM."""
from django.core.management.base import BaseCommand

from apps.accounts.models import User


SAAS_ACCOUNTS = (
    ("saas_owner@hospital-crm.com", "SaaS", "Owner", User.SaaSRole.OWNER),
    ("platform.admin@hospital-crm.com", "Platform", "Admin", User.SaaSRole.PLATFORM_ADMIN),
    ("support.l1@hospital-crm.com", "Support", "L1", User.SaaSRole.SUPPORT_L1),
    ("support.l2@hospital-crm.com", "Support", "L2", User.SaaSRole.SUPPORT_L2),
    ("support.lead@hospital-crm.com", "Support", "Lead", User.SaaSRole.SUPPORT_LEAD),
    ("billing@hospital-crm.com", "Billing", "Team", User.SaaSRole.BILLING),
    ("customer.success@hospital-crm.com", "Customer", "Success", User.SaaSRole.CUSTOMER_SUCCESS),
    ("security.audit@hospital-crm.com", "Security", "Auditor", User.SaaSRole.SECURITY_AUDITOR),
    ("devops@hospital-crm.com", "DevOps", "Engineer", User.SaaSRole.DEVOPS),
)


class Command(BaseCommand):
    help = "Create missing SaaS-company accounts without overwriting existing access."

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True, help="Initial password for newly created accounts only.")

    def handle(self, *args, **options):
        for email, first_name, last_name, role in SAAS_ACCOUNTS:
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_staff": True,
                    "is_saas_admin": True,
                    "saas_role": role,
                },
            )
            if created:
                user.set_password(options["password"])
                user.save()
                self.stdout.write(self.style.SUCCESS(f"Created {email} ({role})."))
            elif not user.saas_role:
                # A legacy platform account gets a role once, but an account
                # already configured by the SaaS Owner is never overwritten.
                user.saas_role = role
                user.is_saas_admin = True
                user.save(update_fields=["saas_role", "is_saas_admin", "is_staff"])
                self.stdout.write(f"Assigned missing SaaS role to {email} ({role}).")
            else:
                self.stdout.write(f"Skipped {email}; existing access is unchanged.")
