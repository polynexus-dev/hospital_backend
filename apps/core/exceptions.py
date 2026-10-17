"""API error handling: some errors raised below the serializer are the
user's to fix, not server faults, so they come back as 400s with a clear
message instead of a 500."""
import re
from decimal import InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError, RestrictedError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler, set_rollback

from .models import FinalizedRecordError

_UNIQUE_MARKERS = ("unique constraint", "duplicate key value")
# Django's message when a lookup gets a non-numeric id, e.g. {"vendor": "abc"}.
_BAD_ID = re.compile(r"^Field '(\w+)' expected a number but got (.+)\.$")


def _in_use_by(exc):
    names = sorted({obj._meta.verbose_name_plural for obj in list(getattr(exc, "protected_objects", None) or getattr(exc, "restricted_objects", None) or [])[:50]})
    return ", ".join(str(n) for n in names) or "other records"


def api_exception_handler(exc, context):
    if isinstance(exc, FinalizedRecordError):
        set_rollback()
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, IntegrityError) and any(m in str(exc).lower() for m in _UNIQUE_MARKERS):
        # e.g. a per-hospital code (employee code, UHID format) the serializer
        # can't check because `hospital` is stamped by the view, not posted.
        set_rollback()
        return Response({"detail": "A record with these values already exists."}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, (ProtectedError, RestrictedError)):
        # Deleting a master record that other records still point to.
        set_rollback()
        return Response({"detail": f"This record is in use by {_in_use_by(exc)} and can't be deleted. Deactivate it instead.",
                         "code": "in_use"}, status=status.HTTP_409_CONFLICT)
    if isinstance(exc, ValueError) and (m := _BAD_ID.match(str(exc))):
        set_rollback()
        return Response({"detail": f"{m.group(2)} is not a valid id — send the record's numeric id."}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, InvalidOperation):
        set_rollback()
        return Response({"detail": "A number was expected (e.g. 1500 or 1500.50)."}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, DjangoValidationError):
        # Model-level checks reached outside a serializer (filters, custom actions).
        set_rollback()
        detail = exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages[0] if len(exc.messages) == 1 else exc.messages}
        return Response(detail, status=status.HTTP_400_BAD_REQUEST)
    return exception_handler(exc, context)
