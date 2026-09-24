from django.core.management.base import BaseCommand

from apps.accounts.role_sync import sync_all_roles


class Command(BaseCommand):
    help = "Give template-based roles their template's permissions for newly added features (also runs after migrate)."

    def handle(self, *args, **options):
        sync_all_roles(stdout=self.stdout)
