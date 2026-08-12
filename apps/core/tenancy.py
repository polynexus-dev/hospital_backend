"""
Row-level multi-tenancy support.

Every hospital (tenant) is a row in `core.Hospital`. Domain models carry a
`hospital` FK via `TenantScopedModel`. Request-scoped code (views, signals,
Celery tasks triggered from a request) reads/writes the "current hospital"
through the contextvar below rather than threading a `hospital` argument
through every call — `TenantMiddleware` sets it once per request.

Management commands, migrations and Celery beat tasks run with no current
hospital set; `TenantManager` treats that as "unscoped" so operational code
can still see everything. Application code that needs to be tenant-safe
(views, serializers) should always go through the middleware-set context
rather than relying on this fallback.
"""
import contextvars
from contextlib import contextmanager

_current_hospital_id: contextvars.ContextVar = contextvars.ContextVar(
    "current_hospital_id", default=None
)


def get_current_hospital_id():
    return _current_hospital_id.get()


def set_current_hospital_id(hospital_id):
    return _current_hospital_id.set(hospital_id)


def reset_current_hospital_id(token):
    _current_hospital_id.reset(token)


@contextmanager
def tenant_context(hospital_id):
    """Run a block of code scoped to a given hospital (e.g. in a Celery task)."""
    token = set_current_hospital_id(hospital_id)
    try:
        yield
    finally:
        reset_current_hospital_id(token)
