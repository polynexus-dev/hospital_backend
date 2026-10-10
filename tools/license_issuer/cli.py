import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from apps.licensing import crypto
from apps.licensing.features import FEATURE_KEYS, label
from apps.licensing.licence import build_payload, new_deployment_id, summary


class IssuerError(Exception):
    pass


# --- keys ---------------------------------------------------------------------

def load_signing_key(key_file: str | None = None):
    if key_file:
        pem = Path(key_file).read_text()
    elif os.environ.get("LICENSE_SIGNING_KEY"):
        pem = os.environ["LICENSE_SIGNING_KEY"].replace("\\n", "\n")
    elif os.environ.get("LICENSE_SIGNING_KEY_PATH"):
        pem = Path(os.environ["LICENSE_SIGNING_KEY_PATH"]).read_text()
    else:
        raise IssuerError("No signing key: set LICENSE_SIGNING_KEY or LICENSE_SIGNING_KEY_PATH, or pass --key-file.")
    return crypto.load_private_key(pem)


def verification_key(key_file: str | None = None):
    """The signing key's public half if we have it, else the product's embedded key."""
    try:
        return load_signing_key(key_file).public_key()
    except IssuerError:
        from apps.licensing import public_key

        if not public_key.PUBLIC_KEY_PEM:
            raise IssuerError("No key to verify with: set LICENSE_SIGNING_KEY(_PATH) or embed the public key.")
        return crypto.load_public_key(public_key.PUBLIC_KEY_PEM)


# --- prompts ------------------------------------------------------------------

class Prompter:
    """Interactive questions; `answers` (a list) replaces stdin in tests."""

    def __init__(self, answers=None, out=sys.stdout):
        self.answers = list(answers) if answers is not None else None
        self.out = out

    def _input(self, question):
        if self.answers is None:
            return input(question)
        self.out.write(question + "\n")
        return self.answers.pop(0)

    def text(self, question, default=None, required=True):
        while True:
            hint = f" [{default}]" if default not in (None, "") else ""
            value = self._input(f"{question}{hint}: ").strip()
            if not value and default is not None:
                return str(default)
            if value or not required:
                return value
            self.out.write("  This is required.\n")

    def date(self, question, default=None, after=None):
        while True:
            value = self.text(question, default.isoformat() if default else None)
            try:
                parsed = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                self.out.write("  Use the format YYYY-MM-DD.\n")
                continue
            if after and parsed <= after:
                self.out.write(f"  Must be after {after.isoformat()}.\n")
                continue
            return parsed

    def integer(self, question, default=None, minimum=0):
        while True:
            value = self.text(question, default)
            try:
                number = int(value)
            except ValueError:
                self.out.write("  Enter a whole number.\n")
                continue
            if number < minimum:
                self.out.write(f"  Must be at least {minimum}.\n")
                continue
            return number

    def yes_no(self, question, default=False):
        while True:
            value = self.text(f"{question} (y/n)", "y" if default else "n").lower()
            if value in ("y", "yes", "n", "no"):
                return value.startswith("y")
            self.out.write("  Answer y or n.\n")

    def features(self):
        """Numbered checklist; all off by default. Enter numbers to toggle, empty to finish."""
        chosen = set()
        while True:
            self.out.write("\nFeatures (enter numbers to toggle, e.g. 1,3,5; 'all'; 'none'; empty line when done):\n")
            for i, key in enumerate(FEATURE_KEYS, 1):
                self.out.write(f"  {i:>2}. [{'x' if key in chosen else ' '}] {label(key)}\n")
            value = self._input("Toggle: ").strip().lower()
            if not value:
                return [k for k in FEATURE_KEYS if k in chosen]
            if value == "all":
                chosen = set(FEATURE_KEYS)
                continue
            if value == "none":
                chosen = set()
                continue
            try:
                picks = [int(p) for p in value.replace(" ", "").split(",") if p]
            except ValueError:
                self.out.write("  Enter numbers separated by commas.\n")
                continue
            for n in picks:
                if 1 <= n <= len(FEATURE_KEYS):
                    chosen ^= {FEATURE_KEYS[n - 1]}
                else:
                    self.out.write(f"  No feature {n}.\n")


# --- commands -----------------------------------------------------------------

def write_licence(payload, key, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(crypto.seal(payload, key) + "\n")


def read_licence(path: Path, key_file=None) -> dict:
    try:
        return crypto.open_license(path.read_text(), verification_key(key_file))
    except crypto.LicenseTampered as exc:
        raise IssuerError(f"{path}: {exc}")


def cmd_new(args, ask: Prompter, out):
    key = load_signing_key(args.key_file)  # fail before asking anything
    out.write("New licence\n-----------\n")
    customer = ask.text("Customer / hospital legal name")
    while True:
        deployment_id = ask.text("Deployment ID (from the customer's config/REQUEST-LICENCE.txt)", default="", required=False)
        if deployment_id:
            break
        if ask.yes_no("No deployment ID entered. Generate a new one? (the customer must then put it in their .env)", default=False):
            deployment_id = new_deployment_id()
            break
    starts = ask.date("Licence start date (YYYY-MM-DD)", default=date.today())
    expires = ask.date("Licence expiry date (YYYY-MM-DD)", default=starts.replace(year=starts.year + 1) - timedelta(days=1), after=starts)
    grace = ask.integer("Grace period in days after expiry", default=14)
    max_users = ask.integer("Maximum active users", minimum=1)
    features = ask.features()
    binding = ask.yes_no("Bind to one machine (hardware binding)?", default=False)
    fingerprint = ask.text("Machine fingerprint printed by the product") if binding else ""
    output = Path(ask.text("Output file", default="license.lic"))

    payload = build_payload(
        customer_name=customer, deployment_id=deployment_id, starts_on=starts, expires_on=expires,
        grace_period_days=grace, max_active_users=max_users, features=features,
        hardware_binding=binding, machine_fingerprint=fingerprint,
    )
    write_licence(payload, key, output)
    out.write(f"\nWrote {output}\n\n{summary(payload)}\n")
    if not args.quiet:
        out.write(f"\nThe installation must have DEPLOYMENT_ID={payload['deployment_id']} in its .env.\n")
    return payload


def cmd_verify(args, ask, out):
    payload = read_licence(Path(args.file), args.key_file)
    expires = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    grace_end = expires + timedelta(days=int(payload.get("grace_period_days") or 0))
    state = "valid" if now <= expires else "in grace period" if now <= grace_end else "EXPIRED"
    out.write(f"Signature OK — licence is {state}.\n\n{summary(payload)}\n")
    return payload


def cmd_renew(args, ask: Prompter, out):
    key = load_signing_key(args.key_file)
    payload = read_licence(Path(args.file), args.key_file)
    old_expiry = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00")).date()
    out.write(f"Renewing {payload['license_id']} ({payload.get('customer_name') or payload.get('hospital_name')}), currently expiring {old_expiry}.\n")
    new_expiry = ask.date("New expiry date (YYYY-MM-DD)", default=old_expiry.replace(year=old_expiry.year + 1), after=old_expiry)
    output = Path(ask.text("Output file", default=str(Path(args.file).with_name(Path(args.file).stem + "-renewed.lic"))))

    renewed = dict(payload)
    renewed["expires_at"] = datetime.combine(new_expiry, datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    # A fresh issue time: installations refuse a licence older than the one they hold.
    renewed["issued_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    write_licence(renewed, key, output)
    out.write(f"\nWrote {output}\n\n{summary(renewed)}\n")
    return renewed


def main(argv=None, answers=None, out=sys.stdout):
    parser = argparse.ArgumentParser(prog="license_issuer", description="Issue, verify and renew product licences.")
    parser.add_argument("--key-file", help="Ed25519 private key PEM (else LICENSE_SIGNING_KEY / LICENSE_SIGNING_KEY_PATH).")
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="Interactive wizard for a new licence.")
    new.add_argument("--quiet", action="store_true")
    sub.add_parser("verify", help="Check a licence file's signature and show what it grants.").add_argument("file")
    sub.add_parser("renew", help="Extend a licence's expiry, keeping its licence ID.").add_argument("file")
    args = parser.parse_args(argv)

    if args.command in ("new", "renew"):
        out.write(
            "Issuing and renewing licences is done in the SaaS console (On-Premise Licences), where only\n"
            "authorised staff can request them, a SaaS Owner approves each one with a 2FA code, and every\n"
            "licence is recorded with who issued it. This tool only verifies licence files:\n"
            "    python -m license_issuer verify <file>\n"
        )
        return 2
    commands = {"verify": cmd_verify}
    try:
        commands[args.command](args, Prompter(answers, out), out)
    except (IssuerError, ValueError) as exc:
        out.write(f"Error: {exc}\n")
        return 1
    except (KeyboardInterrupt, EOFError):
        out.write("\nCancelled.\n")
        return 1
    return 0
