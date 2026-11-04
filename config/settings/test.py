"""
Settings for running the automated test suite (pytest.ini points
DJANGO_SETTINGS_MODULE here). Identical to dev.py except DRF throttling is
disabled.

This is standard practice, not a gap in what gets tested: throttling
itself is covered by dedicated tests that exercise
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] directly (see
apps.accounts.tests). Leaving the real per-minute rates active for the
*whole* suite would make hundreds of tests that legitimately hit the same
endpoint repeatedly (the login flow alone is exercised by half a dozen
tests, all from the same test-client IP) flaky against a limit that exists
to stop brute-force guessing, not to constrain a comprehensive test run.
"""
from .dev import *  # noqa: F401,F403

REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_CLASSES": ()}  # noqa: F405

# The test suite drives the API in plaintext (APIClient never performs the
# ECDH handshake), so payload encryption must be off here regardless of what
# the developer's .env happens to set. Without this pin, a deployment .env
# carrying PAYLOAD_ENCRYPTION_ENABLED=True turns PayloadEncryptionMiddleware
# on under the test settings too, and every /api/ call 401s ("Encryption
# session not found") before reaching the view — failing the whole suite for
# an environment reason unrelated to the code under test.
PAYLOAD_ENCRYPTION_ENABLED = False
