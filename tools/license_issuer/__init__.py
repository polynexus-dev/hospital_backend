"""Offline licence issuer — runs on the platform side only.

    cd Backend/tools
    python -m license_issuer new
    python -m license_issuer verify license.lic
    python -m license_issuer renew license.lic

The signing key comes from LICENSE_SIGNING_KEY (PEM text),
LICENSE_SIGNING_KEY_PATH, or --key-file — never from the repository.
"""
import sys
from pathlib import Path

# Share the product's licence code (pure Python, no Django needed).
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
