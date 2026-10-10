"""The platform's license-verification public key, compiled into the
on-premise image. Deliberately a code constant rather than a setting or an
environment variable: anything a hospital can configure, it could point at
a key of its own and sign its own licenses.

Generate with `python manage.py generate_license_keypair` (run on the
platform side); it rewrites this file. The matching private key goes in
the SaaS deployment's LICENSE_SIGNING_KEY secret and never into this repo.
"""

PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEANm9FVZtxUCUDnwzUTjSJ0f9hpg1sIMv71ZmzqGiek9s=
-----END PUBLIC KEY-----
"""
