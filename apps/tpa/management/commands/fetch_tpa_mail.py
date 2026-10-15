from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Import new insurer / TPA emails into the insurance inbox now (normally every 5 minutes via Celery beat)."

    def handle(self, *args, **options):
        from apps.tpa.mailbox import poll_all

        self.stdout.write(f"Imported {poll_all()} email(s).")
