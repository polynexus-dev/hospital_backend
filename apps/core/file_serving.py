"""
Uploaded files (patient documents, imaging, reports) are only ever served
through an authenticated API view that has already applied the viewset's
permission classes and tenant scoping (SECURITY_COMPLIANCE.md C3). MEDIA_ROOT
is never exposed by nginx or Django, so a guessed or leaked /media/ path
gets nothing.

The response is always a download with a sniff-proof type and a sandboxing
CSP: a crafted upload opened directly in the browser can't run script on our
origin (stored XSS). The SPA fetches the blob and previews it itself.
"""
import os

from django.http import FileResponse, Http404


def stored_file_response(field_file, *, filename=None):
    if not field_file:
        raise Http404("No file.")
    try:
        handle = field_file.open("rb")
    except (FileNotFoundError, OSError) as exc:
        raise Http404("The file is missing from storage.") from exc
    response = FileResponse(handle, as_attachment=True, filename=filename or os.path.basename(field_file.name))
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "private, no-store"
    return response
