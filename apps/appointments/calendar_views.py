"""Connecting doctors' calendars (Settings > Doctor calendars).

GET    /calendar/connections/            doctors you can manage, and their calendar
POST   /calendar/connect/                {"doctor", "provider"} -> {"url"}: open it to sign in
GET    /calendar/callback/<provider>/    where Google / Microsoft send the browser back
PATCH  /calendar/connections/<id>/       {"push_appointments", "block_busy_slots"}
POST   /calendar/connections/<id>/sync/  sync now
DELETE /calendar/connections/<id>/       disconnect

A doctor connects their own calendar; staff who can change doctors
(appointments.change_doctor) can start it for a doctor sitting with them.
"""
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.http import HttpResponseRedirect
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .calendar_sync import PROVIDERS, disconnect, provider_for, store_tokens
from .models import CalendarConnection, Doctor

STATE_SALT = "calendar-connect"
STATE_MAX_AGE = 15 * 60


def _manageable(user):
    doctors = Doctor.objects.filter(hospital_id=user.hospital_id, is_active=True)
    if user.has_perm("appointments.change_doctor"):
        return doctors
    return doctors.filter(user=user)


def _row(doctor):
    conn = getattr(doctor, "calendar_connection", None)
    return {
        "doctor_id": doctor.pk, "doctor": doctor.name, "speciality": doctor.speciality,
        "connection": None if conn is None else {
            "id": conn.pk, "provider": conn.provider, "provider_display": conn.get_provider_display(), "account_email": conn.account_email,
            "status": conn.status, "status_display": conn.get_status_display(), "last_synced_at": conn.last_synced_at,
            "last_error": conn.last_error, "push_appointments": conn.push_appointments, "block_busy_slots": conn.block_busy_slots,
            "busy_slots": doctor.slots.filter(calendar_block__connection=conn).count(),
        },
    }


class CalendarConnectionsView(APIView):
    def get(self, request):
        doctors = _manageable(request.user).select_related("calendar_connection").order_by("name")
        return Response({
            "providers": {key: cls.configured() for key, cls in PROVIDERS.items()},
            "doctors": [_row(d) for d in doctors],
        })


class CalendarConnectView(APIView):
    def post(self, request):
        provider = str(request.data.get("provider") or "")
        if provider not in PROVIDERS:
            return Response({"provider": "Choose google or microsoft."}, status=status.HTTP_400_BAD_REQUEST)
        if not PROVIDERS[provider].configured():
            return Response({"detail": f"{dict(CalendarConnection.Provider.choices)[provider]} isn't set up on this server yet."},
                            status=status.HTTP_400_BAD_REQUEST)
        doctor = _manageable(request.user).filter(pk=request.data.get("doctor")).first()
        if doctor is None:
            return Response({"doctor": "Not a doctor you can connect."}, status=status.HTTP_404_NOT_FOUND)
        nonce = secrets.token_urlsafe(16)
        cache.set(f"calendar-connect:{nonce}", 1, STATE_MAX_AGE)
        state = signing.dumps({"u": request.user.pk, "d": doctor.pk, "p": provider, "n": nonce}, salt=STATE_SALT)
        return Response({"url": provider_for(provider).auth_url(state)})


def _back(result, reason=""):
    query = {"tab": "calendars", "calendar": result, **({"reason": reason[:200]} if reason else {})}
    return HttpResponseRedirect(f"{settings.PUBLIC_APP_URL.rstrip('/')}/settings?{urlencode(query)}")


class CalendarCallbackView(APIView):
    """The browser comes back here from Google / Microsoft with ?code&state.
    No staff session on this request: the signed, one-time state says who
    started it and for which doctor."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, provider):
        from apps.accounts.models import User

        if request.query_params.get("error"):
            return _back("cancelled", request.query_params.get("error_description") or "Sign-in was cancelled.")
        try:
            state = signing.loads(request.query_params.get("state", ""), salt=STATE_SALT, max_age=STATE_MAX_AGE)
        except signing.BadSignature:
            return _back("error", "This sign-in link has expired. Start again.")
        if state.get("p") != provider or provider not in PROVIDERS or not cache.delete(f"calendar-connect:{state.get('n')}"):
            return _back("error", "This sign-in link has already been used. Start again.")
        user = User.objects.filter(pk=state["u"], is_active=True).first()
        doctor = _manageable(user).filter(pk=state["d"]).first() if user is not None else None
        if doctor is None:
            return _back("error", "You can no longer connect this doctor's calendar.")
        try:
            tokens, email = provider_for(provider).exchange(request.query_params.get("code", ""))
        except Exception as exc:  # noqa: BLE001 - network or provider error: show the reason, never a 500 page
            return _back("error", str(exc) or "The calendar sign-in failed.")
        if not tokens.get("refresh_token"):
            return _back("error", "The calendar didn't grant offline access. Remove Polynexus from the account's connected apps and try again.")

        old = CalendarConnection.objects.filter(doctor=doctor).first()
        if old is not None and (old.provider != provider or old.account_email.lower() != email.lower()):
            disconnect(old)
            old = None
        conn = old or CalendarConnection(hospital_id=doctor.hospital_id, doctor=doctor, provider=provider)
        conn.account_email, conn.connected_by = email, user
        conn.status, conn.last_error = CalendarConnection.Status.ACTIVE, ""
        store_tokens(conn, tokens)
        conn.save()
        _queue_sync(conn)
        return _back("connected")


def _queue_sync(conn):
    from .tasks import sync_doctor_calendar

    try:
        sync_doctor_calendar.delay(conn.pk)
    except Exception:
        pass  # the five-minute sync will pick it up


class CalendarConnectionDetailView(APIView):
    def _conn(self, request, pk):
        return CalendarConnection.objects.filter(pk=pk, doctor__in=_manageable(request.user)).select_related("doctor").first()

    def patch(self, request, pk):
        conn = self._conn(request, pk)
        if conn is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        for name in ("push_appointments", "block_busy_slots"):
            if name in request.data:
                setattr(conn, name, bool(request.data[name]))
        conn.save(update_fields=["push_appointments", "block_busy_slots", "updated_at"])
        _queue_sync(conn)
        return Response(_row(conn.doctor))

    def delete(self, request, pk):
        conn = self._conn(request, pk)
        if conn is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        disconnect(conn)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CalendarSyncNowView(CalendarConnectionDetailView):
    def post(self, request, pk):
        from .calendar_sync import sync_connection

        conn = self._conn(request, pk)
        if conn is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        result = sync_connection(conn)
        conn.refresh_from_db()
        return Response({"result": result, **_row(conn.doctor)}, status=status.HTTP_200_OK if "error" not in result else status.HTTP_502_BAD_GATEWAY)
