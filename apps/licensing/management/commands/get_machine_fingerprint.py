from django.core.management.base import BaseCommand

from apps.licensing.fingerprint import components, in_container, machine_fingerprint


class Command(BaseCommand):
    help = "Print this server's machine fingerprint, to send to support when requesting a license."

    def add_arguments(self, parser):
        parser.add_argument("--verbose-parts", action="store_true", help="Also show which identifiers were found.")

    def handle(self, *args, **options):
        if options["verbose_parts"]:
            for name, value in components().items():
                self.stdout.write(f"  {name}: {value or '(not found)'}")
            if in_container():
                self.stdout.write("  (running in a container: MAC address not used)")
        self.stdout.write(machine_fingerprint())
