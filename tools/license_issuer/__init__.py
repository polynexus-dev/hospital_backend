"""Licence file checker (platform side).

    cd Backend/tools
    python -m license_issuer verify license.lic

Issuing and renewing happen only in the SaaS console, so every licence is
authorised, approved by a SaaS Owner with 2FA, and recorded with its issuer.
The signing key lives only on the SaaS server.
"""
import sys
from pathlib import Path

# Share the product's licence code (pure Python, no Django needed).
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
