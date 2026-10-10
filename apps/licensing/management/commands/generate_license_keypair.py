from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.management.base import BaseCommand, CommandError

from apps.licensing.crypto import private_pem, public_pem

PUBLIC_KEY_MODULE = Path(__file__).resolve().parents[2] / "public_key.py"


class Command(BaseCommand):
    help = (
        "Platform side, once: create the Ed25519 key pair for signing licenses. Writes the private key "
        "to --private-key-out (store it as the SaaS LICENSE_SIGNING_KEY secret — never commit it) and "
        "embeds the public key in apps/licensing/public_key.py for on-premise builds."
    )

    def add_arguments(self, parser):
        parser.add_argument("--private-key-out", required=True, help="Where to write the private key PEM file (outside the repo).")
        parser.add_argument("--force", action="store_true", help="Replace an existing key pair. Every issued license stops verifying.")

    def handle(self, *args, **options):
        out = Path(options["private_key_out"])
        if out.is_dir():
            raise CommandError(f"{out} is a folder. Give a file path, e.g. {out / 'license_signing_key.pem'}")
        if out.exists() and not options["force"]:
            raise CommandError(f"{out} already exists. Use --force to replace the key pair.")
        current = PUBLIC_KEY_MODULE.read_text()
        if "PUBLIC_KEY_PEM = None" not in current and not options["force"]:
            raise CommandError("apps/licensing/public_key.py already holds a key. Use --force to replace it.")

        key = Ed25519PrivateKey.generate()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(private_pem(key))
        try:
            out.chmod(0o600)
        except OSError:
            pass
        head = current.split("PUBLIC_KEY_PEM =")[0]
        PUBLIC_KEY_MODULE.write_text(f'{head}PUBLIC_KEY_PEM = """{public_pem(key)}"""\n')
        self.stdout.write(self.style.SUCCESS(f"Private key written to {out}"))
        self.stdout.write("Public key embedded in apps/licensing/public_key.py - commit that file.")
