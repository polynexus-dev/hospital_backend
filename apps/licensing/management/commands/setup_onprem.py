import getpass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.licensing import service


class Command(BaseCommand):
    help = (
        "On-premise first run: verify and install the license, then create the licensed hospital "
        "and its owner account. Safe to re-run: an existing hospital is left as it is."
    )

    def add_arguments(self, parser):
        parser.add_argument("--license", required=True, help="Path to the .lic file.")
        parser.add_argument("--admin-email", help="Owner account email (prompted if omitted).")
        parser.add_argument("--admin-password", help="Owner account password (prompted if omitted).")

    def handle(self, *args, **options):
        if not service.is_on_premise():
            raise CommandError("Set DEPLOYMENT_MODE=on_premise for an on-premise installation.")
        try:
            blob = Path(options["license"]).read_text()
        except OSError as exc:
            raise CommandError(f"Can't read the license file: {exc}")

        status = service.verify_license(blob)
        if not status.usable:
            raise CommandError(f"License not accepted ({status.state}): {status.message}\nThis server's fingerprint: {status.fingerprint}")
        payload = status.payload

        from apps.core.models import Hospital

        with transaction.atomic():
            service.install_license(blob)
            hospital = Hospital.objects.filter(pk=payload["hospital_id"]).first()
            if hospital is None:
                email = options["admin_email"] or input("Owner email: ").strip()
                password = options["admin_password"] or getpass.getpass("Owner password: ")
                if not email or not password:
                    raise CommandError("An owner email and password are required.")
                from apps.saas_admin.tenant_service import onboard_hospital_tenant

                hospital = onboard_hospital_tenant({
                    "id": payload["hospital_id"],
                    "is_on_premise": True,
                    "name": payload["hospital_name"],
                    "slug": slugify(payload["hospital_name"])[:60] or "hospital",
                    "enabled_modules": payload.get("enabled_modules") or None,
                    "owner": {"email": email, "password": password},
                    # The license caps users on-premise, not the SaaS subscription.
                    "subscription": {"max_staff_users": 0},
                })["hospital"]
                self.stdout.write(self.style.SUCCESS(f"Created {hospital.name} and owner account {email}."))
            else:
                self.stdout.write(f"{hospital.name} already exists; left unchanged.")

        self.stdout.write(self.style.SUCCESS(f"License {payload['license_id']} installed - valid until {payload['expires_at']}."))
