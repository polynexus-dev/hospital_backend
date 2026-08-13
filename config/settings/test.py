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
