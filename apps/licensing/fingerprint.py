"""The machine fingerprint a license is bound to.

SHA-256 over stable host identifiers:
  - /etc/machine-id (or /var/lib/dbus/machine-id)
  - /sys/class/dmi/id/product_uuid (system UUID; needs root or a mounted copy)
  - the primary network interface's MAC address — skipped inside containers,
    where it changes every time the container is recreated

In Docker, mount the host's files read-only under LICENSE_HOST_ROOT (see
deploy/onprem/docker-compose.onprem.yml) so the fingerprint identifies the
server, not the container. Run `python manage.py get_machine_fingerprint`
the same way the server runs to get the value to send for licensing.
"""
import hashlib
import os
import platform
import uuid
from pathlib import Path

from django.conf import settings

MACHINE_ID_PATHS = ("etc/machine-id", "var/lib/dbus/machine-id")
PRODUCT_UUID_PATH = "sys/class/dmi/id/product_uuid"


def _host_path(relative: str) -> Path:
    root = getattr(settings, "LICENSE_HOST_ROOT", "") or "/"
    return Path(root) / relative


def _read(relative: str) -> str:
    try:
        return _host_path(relative).read_text().strip().lower()
    except OSError:
        return ""


def in_container() -> bool:
    return os.path.exists("/.dockerenv") or os.environ.get("container") is not None


def _primary_mac() -> str:
    net = Path("/sys/class/net")
    if net.is_dir():
        for iface in sorted(p.name for p in net.iterdir()):
            if iface == "lo" or iface.startswith(("docker", "veth", "br-", "virbr")):
                continue
            try:
                mac = (net / iface / "address").read_text().strip().lower()
            except OSError:
                continue
            if mac and mac != "00:00:00:00:00:00":
                return mac
    node = uuid.getnode()
    return "" if (node >> 40) & 1 else f"{node:012x}"  # bit set = randomly generated, not a real MAC


def components() -> dict:
    """The identifiers the fingerprint is built from (shown by the command)."""
    machine_id = next((v for v in (_read(p) for p in MACHINE_ID_PATHS) if v), "")
    parts = {
        "machine_id": machine_id,
        "product_uuid": _read(PRODUCT_UUID_PATH),
        "mac": "" if in_container() else _primary_mac(),
    }
    if not any(parts.values()):  # e.g. a Windows/macOS development machine
        parts["hostname"] = platform.node().lower()
    return parts


def machine_fingerprint() -> str:
    parts = components()
    material = "|".join(f"{k}={v}" for k, v in sorted(parts.items()))
    return hashlib.sha256(material.encode()).hexdigest()
