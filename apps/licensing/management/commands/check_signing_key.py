from django.core.management.base import BaseCommand, CommandError

from apps.licensing.issuing import SigningKeyMissing, signing_key


class Command(BaseCommand):
    help = "Check this server can sign licences: the key is set, readable, valid, and matches the product's public key."

    def handle(self, *args, **options):
        try:
            signing_key()
        except SigningKeyMissing as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS("Licence signing key OK: it matches the public key built into the product."))
