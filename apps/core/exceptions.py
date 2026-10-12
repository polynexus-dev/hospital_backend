"""API error handling: some errors raised below the serializer are the
user's to fix, not server faults, so they come back as 400s with a clear
message instead of a 500."""
from django.db import IntegrityError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler, set_rollback

from .models import FinalizedRecordError

_UNIQUE_MARKERS = ("unique constraint", "duplicate key value")


def api_exception_handler(exc, context):
    if isinstance(exc, FinalizedRecordError):
        set_rollback()
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, IntegrityError) and any(m in str(exc).lower() for m in _UNIQUE_MARKERS):
        # e.g. a per-hospital code (employee code, UHID format) the serializer
        # can't check because `hospital` is stamped by the view, not posted.
        set_rollback()
        return Response({"detail": "A record with these values already exists."}, status=status.HTTP_400_BAD_REQUEST)
    return exception_handler(exc, context)
