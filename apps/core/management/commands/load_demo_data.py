"""Load the Polynexus demo dataset (apps/core/demo_seed) into the demo-hospital tenant.

About 245 patients, 730 appointments, admissions, lab/radiology, pharmacy,
CRM, ERP, HR and the other modules, all fictional (+91 90000 xxxxx phones,
@demo.polynexus.in emails), dated around the day it runs. Staff logins share
the password Demo@Polynexus2026 (see apps/core/demo_seed/common.py).

    python manage.py load_demo_data

Runs on start-up from docker-entrypoint.sh (never from the on-premise
entrypoint). Safe to re-run: once loaded it does nothing, and a failed run
rolls back completely. The data is written through the ORM, so it is
encrypted with this server's own field-encryption keys.
"""
import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.test.utils import override_settings

STEPS = ["ipd", "diagnostics", "pharmacy", "crm", "erp", "extras", "history", "callbacks", "phones", "inbox", "clinical"]


class Command(BaseCommand):
    help = "Load the Polynexus demo dataset into the demo-hospital tenant (skips if already loaded)."

    def log(self, msg):
        self.stdout.write(f"[demo {time.strftime('%H:%M:%S')}] {msg}")

    def handle(self, *args, **options):
        from apps.automation.models import Workflow
        from apps.core.models import Hospital
        from apps.core.demo_seed.common import HOSPITAL_NAME

        h = Hospital.objects.filter(slug="demo-hospital").first()
        if h is None:
            self.log("demo-hospital tenant missing - running seed_demo_data first")
            call_command("seed_demo_data")
            h = Hospital.objects.get(slug="demo-hospital")
        if h.name == HOSPITAL_NAME:
            self.log("Demo data already loaded - nothing to do.")
            return

        # Anything the seed queues stays in memory and is never run, so seeding
        # can't send WhatsApp/SMS/email.
        from config.celery import app as celery_app

        celery_app.conf.update(broker_url="memory://", result_backend="cache+memory://", task_always_eager=False)

        active = list(Workflow.objects.filter(is_active=True).values_list("pk", flat=True))
        Workflow.objects.update(is_active=False)  # paused while seeding
        try:
            # A few steps call the API in-process (APIClient) to get exactly what
            # the screens show; let those through whatever the deployment's settings.
            with override_settings(
                ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False, PAYLOAD_ENCRYPTION_ENABLED=False,
                REST_FRAMEWORK={**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_CLASSES": ()},
                EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            ):
                self.seed()
        except BaseException:
            Workflow.objects.filter(pk__in=active).update(is_active=True)
            self.log("FAILED - nothing was added.")
            raise
        Workflow.objects.filter(pk__in=active).update(is_active=True)
        # the demo workflows go live only now, so seeding never triggered them
        Workflow.objects.filter(hospital__slug="demo-hospital").update(is_active=True)
        self.log("done")

    @transaction.atomic
    def seed(self):
        from importlib import import_module

        from apps.core.demo_seed import core, opd

        h = core.setup_hospital()
        # demo tenant: no idle screen lock while people click around (re-enable under Governance → Security policy)
        from apps.governance.models import SecurityPolicy

        SecurityPolicy.objects.filter(pk=SecurityPolicy.for_hospital(h).pk).update(idle_lock_enabled=False)
        # the demo seeds ~55 staff logins; leave room to add users from Admin → Users
        from apps.saas_admin.models import TenantSubscription

        TenantSubscription.objects.filter(hospital=h, max_staff_users__gt=0, max_staff_users__lt=100).update(max_staff_users=100)
        core.fix_base_roles(h)
        core.tidy_base_doctors(h)
        depts = core.departments(h)
        users = core.staff(h, depts)
        docs = core.doctors(h, depts)
        pts = core.patients(h)
        self.log(f"core: {len(docs)} doctors, {sum(len(v) for v in users.values())} staff, {len(pts)} patients")
        tariffs = opd.tariffs(h)
        opd.schedules(h, docs)
        self.log(f"opd: {opd.appointments(h, docs, pts, users, tariffs)}")
        ctx = {"h": h, "depts": depts, "users": users, "docs": docs, "pts": pts, "tariffs": tariffs}
        for name in STEPS:
            self.log(f"{name}: {import_module(f'apps.core.demo_seed.{name}').run(ctx)}")
