"""Two-way sync between OPD appointments and doctors' own calendars
(Google Calendar, Microsoft 365 / Outlook).

Out: every appointment becomes an event in the doctor's calendar the moment
it's booked, and is moved / removed when it changes or is cancelled. The
event shows the patient's initials and the reason only (e.g. "OPD: A.P.
(follow-up)") plus a link into the app — no names, numbers or clinical
detail leave our system.

In: every SYNC_INTERVAL the doctor's busy events (meetings, leave, OT,
anything not marked free) over the slot horizon are read, and the OPD slots
they overlap are blocked so nobody books them; when the event moves or is
deleted the slots open again. Only times are stored, never titles. A busy
event over an already-booked appointment raises a task for the front desk
instead of touching the booking.

Both providers are plain OAuth 2.0 + REST, called with `requests`:
settings GOOGLE_CALENDAR_CLIENT_ID / _SECRET, MICROSOFT_CALENDAR_CLIENT_ID /
_SECRET / _TENANT, and CALENDAR_OAUTH_REDIRECT_BASE (the public URL the
providers redirect back to: <base>/api/v1/calendar/callback/<provider>/).
"""
import base64
import json
import logging
from datetime import datetime, time, timedelta, timezone as dt_timezone
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Appointment, CalendarAppointmentEvent, CalendarBusyBlock, CalendarConnection, Slot
from .services import WEEKS_TO_GENERATE_AHEAD

logger = logging.getLogger(__name__)

TIMEOUT = 15
BUSY_REASON = "Busy in the doctor's calendar"
INACTIVE = [Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED]


class CalendarError(Exception):
    pass


class AccessRevoked(CalendarError):
    """The doctor withdrew access (or the refresh token expired): reconnect."""


def redirect_uri(provider):
    base = (settings.CALENDAR_OAUTH_REDIRECT_BASE or settings.PUBLIC_APP_URL).rstrip("/")
    return f"{base}/api/v1/calendar/callback/{provider}/"


def _iso(dt):
    return dt.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check(response, what):
    if response.status_code == 401:
        raise AccessRevoked(f"{what}: access was withdrawn. Reconnect the calendar.")
    if response.status_code >= 400:
        try:
            detail = response.json().get("error")
            detail = detail.get("message") if isinstance(detail, dict) else detail
        except ValueError:
            detail = response.text[:120]
        raise CalendarError(f"{what} failed ({response.status_code}): {str(detail)[:150]}")
    return response.json() if response.content else {}


# --- Providers ---------------------------------------------------------------


class Google:
    key = CalendarConnection.Provider.GOOGLE
    AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN = "https://oauth2.googleapis.com/token"
    API = "https://www.googleapis.com/calendar/v3"
    SCOPES = "openid email https://www.googleapis.com/auth/calendar.events"

    @staticmethod
    def configured():
        return bool(settings.GOOGLE_CALENDAR_CLIENT_ID and settings.GOOGLE_CALENDAR_CLIENT_SECRET)

    def auth_url(self, state):
        return f"{self.AUTH}?" + urlencode({
            "client_id": settings.GOOGLE_CALENDAR_CLIENT_ID, "redirect_uri": redirect_uri(self.key), "response_type": "code",
            "scope": self.SCOPES, "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
        })

    def exchange(self, code):
        data = _check(requests.post(self.TOKEN, data={
            "code": code, "client_id": settings.GOOGLE_CALENDAR_CLIENT_ID, "client_secret": settings.GOOGLE_CALENDAR_CLIENT_SECRET,
            "redirect_uri": redirect_uri(self.key), "grant_type": "authorization_code",
        }, timeout=TIMEOUT), "Google sign-in")
        email = ""
        if data.get("id_token"):  # straight from Google's token endpoint over TLS: no signature check needed to read it
            payload = data["id_token"].split(".")[1]
            email = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email", "")
        return data, email

    def refresh(self, refresh_token):
        response = requests.post(self.TOKEN, data={
            "refresh_token": refresh_token, "client_id": settings.GOOGLE_CALENDAR_CLIENT_ID,
            "client_secret": settings.GOOGLE_CALENDAR_CLIENT_SECRET, "grant_type": "refresh_token",
        }, timeout=TIMEOUT)
        if response.status_code == 400 and "invalid_grant" in response.text:
            raise AccessRevoked("Google access was withdrawn. Reconnect the calendar.")
        return _check(response, "Google token refresh")

    def _events(self, conn):
        return f"{self.API}/calendars/{requests.utils.quote(conn.calendar_id or 'primary', safe='')}/events"

    def upsert_event(self, conn, token, event_id, ev):
        body = {
            "summary": ev["summary"], "description": ev["description"],
            "start": {"dateTime": _iso(ev["start"])}, "end": {"dateTime": _iso(ev["end"])},
            "transparency": "opaque", "reminders": {"useDefault": True},
            "extendedProperties": {"private": {"polynexus_appointment": str(ev["appointment_id"])}},
        }
        headers = {"Authorization": f"Bearer {token}"}
        if event_id:
            response = requests.patch(f"{self._events(conn)}/{event_id}", json=body, headers=headers, timeout=TIMEOUT)
            if response.status_code not in (404, 410):
                return _check(response, "Updating the Google event")["id"]
        return _check(requests.post(self._events(conn), json=body, headers=headers, timeout=TIMEOUT), "Creating the Google event")["id"]

    def delete_event(self, conn, token, event_id):
        response = requests.delete(f"{self._events(conn)}/{event_id}", headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        if response.status_code not in (404, 410):
            _check(response, "Removing the Google event")

    def busy_events(self, conn, token, start, end):
        params = {"timeMin": _iso(start), "timeMax": _iso(end), "singleEvents": "true", "maxResults": 250,
                  "fields": "nextPageToken,items(id,status,start,end,transparency,eventType,attendees(self,responseStatus),extendedProperties)"}
        out = []
        while True:
            data = _check(requests.get(self._events(conn), params=params, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT),
                          "Reading the Google calendar")
            for item in data.get("items", []):
                if item.get("status") == "cancelled" or item.get("transparency") == "transparent":
                    continue
                if item.get("eventType") == "workingLocation" or (item.get("extendedProperties") or {}).get("private", {}).get("polynexus_appointment"):
                    continue
                if any(a.get("self") and a.get("responseStatus") == "declined" for a in item.get("attendees") or []):
                    continue
                span = _google_span(item)
                if span:
                    out.append((item["id"], *span))
            if not data.get("nextPageToken"):
                return out
            params["pageToken"] = data["nextPageToken"]

    def revoke(self, conn):
        token = conn.refresh_token or conn.access_token
        if token:
            requests.post("https://oauth2.googleapis.com/revoke", params={"token": token}, timeout=TIMEOUT)


def _google_span(item):
    start, end = item.get("start") or {}, item.get("end") or {}
    if start.get("dateTime") and end.get("dateTime"):
        return datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")), datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
    if start.get("date") and end.get("date"):  # all-day: the whole local day(s)
        tz = timezone.get_current_timezone()
        return (timezone.make_aware(datetime.combine(datetime.fromisoformat(start["date"]).date(), time.min), tz),
                timezone.make_aware(datetime.combine(datetime.fromisoformat(end["date"]).date(), time.min), tz))
    return None


class Microsoft:
    key = CalendarConnection.Provider.MICROSOFT
    API = "https://graph.microsoft.com/v1.0"
    SCOPES = "offline_access openid email User.Read Calendars.ReadWrite"
    BUSY = {"busy", "oof", "tentative"}

    @staticmethod
    def configured():
        return bool(settings.MICROSOFT_CALENDAR_CLIENT_ID and settings.MICROSOFT_CALENDAR_CLIENT_SECRET)

    def _login(self, path):
        return f"https://login.microsoftonline.com/{settings.MICROSOFT_CALENDAR_TENANT}/oauth2/v2.0/{path}"

    def auth_url(self, state):
        return self._login("authorize") + "?" + urlencode({
            "client_id": settings.MICROSOFT_CALENDAR_CLIENT_ID, "redirect_uri": redirect_uri(self.key), "response_type": "code",
            "response_mode": "query", "scope": self.SCOPES, "state": state, "prompt": "select_account",
        })

    def _token(self, **data):
        response = requests.post(self._login("token"), data={
            "client_id": settings.MICROSOFT_CALENDAR_CLIENT_ID, "client_secret": settings.MICROSOFT_CALENDAR_CLIENT_SECRET,
            "scope": self.SCOPES, **data,
        }, timeout=TIMEOUT)
        if response.status_code == 400 and "invalid_grant" in response.text:
            raise AccessRevoked("Microsoft access was withdrawn. Reconnect the calendar.")
        return _check(response, "Microsoft sign-in")

    def exchange(self, code):
        data = self._token(grant_type="authorization_code", code=code, redirect_uri=redirect_uri(self.key))
        me = _check(requests.get(f"{self.API}/me", params={"$select": "mail,userPrincipalName"},
                                 headers={"Authorization": f"Bearer {data['access_token']}"}, timeout=TIMEOUT), "Reading the Microsoft account")
        return data, me.get("mail") or me.get("userPrincipalName") or ""

    def refresh(self, refresh_token):
        return self._token(grant_type="refresh_token", refresh_token=refresh_token)

    def upsert_event(self, conn, token, event_id, ev):
        body = {
            "subject": ev["summary"], "body": {"contentType": "text", "content": ev["description"]},
            "start": {"dateTime": _iso(ev["start"])[:-1], "timeZone": "UTC"}, "end": {"dateTime": _iso(ev["end"])[:-1], "timeZone": "UTC"},
            "showAs": "busy", "isReminderOn": True,
        }
        headers = {"Authorization": f"Bearer {token}"}
        if event_id:
            response = requests.patch(f"{self.API}/me/events/{event_id}", json=body, headers=headers, timeout=TIMEOUT)
            if response.status_code != 404:
                return _check(response, "Updating the Outlook event")["id"]
        return _check(requests.post(f"{self.API}/me/events", json=body, headers=headers, timeout=TIMEOUT), "Creating the Outlook event")["id"]

    def delete_event(self, conn, token, event_id):
        response = requests.delete(f"{self.API}/me/events/{event_id}", headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        if response.status_code != 404:
            _check(response, "Removing the Outlook event")

    def busy_events(self, conn, token, start, end):
        url = f"{self.API}/me/calendarView"
        params = {"startDateTime": _iso(start), "endDateTime": _iso(end), "$top": 200,
                  "$select": "id,start,end,showAs,isCancelled,responseStatus"}
        headers = {"Authorization": f"Bearer {token}", "Prefer": 'outlook.timezone="UTC"'}
        out = []
        while url:
            data = _check(requests.get(url, params=params, headers=headers, timeout=TIMEOUT), "Reading the Outlook calendar")
            for item in data.get("value", []):
                if item.get("isCancelled") or item.get("showAs") not in self.BUSY:
                    continue
                if (item.get("responseStatus") or {}).get("response") == "declined":
                    continue
                s, e = item["start"]["dateTime"], item["end"]["dateTime"]
                out.append((item["id"], _utc(s), _utc(e)))
            url, params = data.get("@odata.nextLink"), None
        return out

    def revoke(self, conn):
        pass  # Microsoft has no token revocation endpoint; we just forget the tokens


def _utc(value):
    value = value.rstrip("Z")
    if "." in value:
        head, frac = value.split(".", 1)
        value = f"{head}.{frac[:6]}"
    return datetime.fromisoformat(value).replace(tzinfo=dt_timezone.utc)


PROVIDERS = {Google.key: Google, Microsoft.key: Microsoft}


def provider_for(conn_or_key):
    key = getattr(conn_or_key, "provider", conn_or_key)
    return PROVIDERS[key]()


# --- Tokens ------------------------------------------------------------------


def access_token(conn):
    if conn.access_token and conn.token_expires_at and conn.token_expires_at > timezone.now() + timedelta(minutes=2):
        return conn.access_token
    if not conn.refresh_token:
        raise AccessRevoked("No refresh token. Reconnect the calendar.")
    data = provider_for(conn).refresh(conn.refresh_token)
    conn.access_token = data["access_token"]
    conn.refresh_token = data.get("refresh_token") or conn.refresh_token
    conn.token_expires_at = timezone.now() + timedelta(seconds=int(data.get("expires_in") or 3600))
    conn.save(update_fields=["access_token", "refresh_token", "token_expires_at", "updated_at"])
    return conn.access_token


def store_tokens(conn, data):
    conn.access_token = data["access_token"]
    conn.refresh_token = data.get("refresh_token") or conn.refresh_token
    conn.token_expires_at = timezone.now() + timedelta(seconds=int(data.get("expires_in") or 3600))


def _fail(conn, exc):
    conn.status = CalendarConnection.Status.REVOKED if isinstance(exc, AccessRevoked) else CalendarConnection.Status.ERROR
    conn.last_error = str(exc)[:255]
    conn.save(update_fields=["status", "last_error", "updated_at"])


def _ok(conn):
    conn.status, conn.last_error, conn.last_synced_at = CalendarConnection.Status.ACTIVE, "", timezone.now()
    conn.save(update_fields=["status", "last_error", "last_synced_at", "updated_at"])


# --- Appointments -> calendar ------------------------------------------------


def initials(name):
    parts = [p for p in (name or "").replace(".", " ").split() if p[:1].isalpha()]
    return ".".join(p[0].upper() for p in parts[:3]) + "." if parts else "Patient"


def slot_span(slot):
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(slot.date, slot.start_time), tz),
            timezone.make_aware(datetime.combine(slot.date, slot.end_time), tz))


def event_content(appointment):
    start, end = slot_span(appointment.slot)
    reason = (appointment.reason or "consultation").strip()[:60]
    link = f"{settings.PUBLIC_APP_URL.rstrip('/')}/appointments?appointment={appointment.pk}"
    return {
        "appointment_id": appointment.pk, "start": start, "end": end,
        "summary": f"OPD: {initials(appointment.patient.full_name)} ({reason})",
        "description": f"OPD appointment #{appointment.pk} at {appointment.hospital.name}.\nOpen in Polynexus: {link}\n"
                       "Patient details stay in Polynexus and are not copied to this calendar.",
    }


def sync_appointment(appointment):
    """Creates, moves or removes the calendar event for one appointment.
    Returns "created" / "updated" / "deleted" / "skipped"."""
    conn = CalendarConnection.objects.filter(doctor_id=appointment.doctor_id, status=CalendarConnection.Status.ACTIVE, push_appointments=True).first()
    link = CalendarAppointmentEvent.objects.filter(appointment=appointment).select_related("connection").first()
    if link is not None and (conn is None or link.connection_id != conn.pk):
        link.delete()  # a different (or no) calendar now: the old event isn't ours to manage any more
        link = None
    if conn is None:
        return "skipped"
    provider = provider_for(conn)
    try:
        token = access_token(conn)
        if appointment.status in INACTIVE:
            if link is None:
                return "skipped"
            provider.delete_event(conn, token, link.external_event_id)
            link.delete()
            return "deleted"
        event_id = provider.upsert_event(conn, token, link.external_event_id if link else None, event_content(appointment))
    except requests.RequestException as exc:
        _fail(conn, CalendarError(f"Couldn't reach the calendar: {type(exc).__name__}"))
        raise CalendarError(str(exc)) from exc
    except CalendarError as exc:
        _fail(conn, exc)
        raise
    CalendarAppointmentEvent.objects.update_or_create(appointment=appointment, defaults={
        "hospital_id": appointment.hospital_id, "connection": conn, "external_event_id": event_id})
    return "updated" if link else "created"


# --- Calendar -> slots -------------------------------------------------------


def _window():
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(today, time.min), tz)
    return start, start + timedelta(days=WEEKS_TO_GENERATE_AHEAD * 7 + 1)


def _conflict_task(slot, appointment):
    from django.contrib.contenttypes.models import ContentType

    from apps.automation.models import Task

    # Titles aren't encrypted: no patient names.
    title = f"Calendar clash: {slot.doctor.name} is busy at {slot.date:%d %b} {slot.start_time:%H:%M} (appointment #{appointment.pk})"
    if Task.objects.filter(hospital_id=slot.hospital_id, title=title, status=Task.Status.PENDING).exists():
        return False
    Task.objects.create(
        hospital_id=slot.hospital_id, title=title[:255], priority=Task.Priority.HIGH, department_id=slot.doctor.department_id,
        description="The doctor's own calendar now shows them busy at a time a patient is booked. Confirm with the doctor and reschedule if needed.",
        due_at=timezone.now() + timedelta(hours=2), content_type=ContentType.objects.get_for_model(appointment), object_id=appointment.pk,
    )
    return True


def sync_busy(conn):
    """Reads the doctor's busy times and blocks / unblocks their slots.
    Returns {"blocked", "unblocked", "clashes"}."""
    start, end = _window()
    token = access_token(conn)
    ours = set(CalendarAppointmentEvent.objects.filter(connection=conn).values_list("external_event_id", flat=True))
    events = [e for e in provider_for(conn).busy_events(conn, token, start, end) if e[0] not in ours and e[2] > e[1]]

    with transaction.atomic():
        current = {}
        for event_id, starts, ends in events:
            block, _ = CalendarBusyBlock.objects.update_or_create(connection=conn, external_event_id=event_id, defaults={
                "hospital_id": conn.hospital_id, "starts_at": starts, "ends_at": ends})
            current[block.pk] = block
        stale = list(CalendarBusyBlock.objects.filter(connection=conn).exclude(pk__in=current).values_list("pk", flat=True))

        blocked = unblocked = clashes = 0
        slots = Slot.objects.select_for_update().filter(doctor_id=conn.doctor_id, date__gte=start.date(), date__lte=end.date()).select_related("doctor")
        booked = {a.slot_id: a for a in Appointment.objects.filter(slot__in=slots).exclude(status__in=INACTIVE)}
        blocks = list(current.values()) if conn.block_busy_slots else []
        for slot in slots:
            s, e = slot_span(slot)
            hit = next((b for b in blocks if b.starts_at < e and b.ends_at > s), None)
            if hit is not None:
                if slot.pk in booked:
                    clashes += _conflict_task(slot, booked[slot.pk])
                elif not slot.is_blocked:
                    Slot.objects.filter(pk=slot.pk).update(is_blocked=True, blocked_reason=BUSY_REASON, calendar_block=hit)
                    blocked += 1
                elif slot.calendar_block_id and slot.calendar_block_id != hit.pk:  # the old event went, another still covers it
                    Slot.objects.filter(pk=slot.pk).update(calendar_block=hit)
                # else: blocked by hand (leave, OT) - not ours to change
            elif slot.calendar_block_id:  # only lift blocks the calendar made
                Slot.objects.filter(pk=slot.pk).update(is_blocked=False, blocked_reason="", calendar_block=None)
                unblocked += 1
        CalendarBusyBlock.objects.filter(pk__in=stale).delete()
    return {"blocked": blocked, "unblocked": unblocked, "clashes": clashes}


def sync_connection(conn):
    """Full sync: busy times in, plus any upcoming appointment that isn't
    in the calendar yet (e.g. right after connecting)."""
    try:
        result = sync_busy(conn)
        pushed = 0
        if conn.push_appointments:
            missing = (Appointment.objects.filter(doctor_id=conn.doctor_id, slot__date__gte=timezone.localdate(), calendar_event__isnull=True)
                       .exclude(status__in=INACTIVE).select_related("slot", "patient", "hospital")[:500])
            for appointment in missing:
                sync_appointment(appointment)
                pushed += 1
    except requests.RequestException as exc:
        _fail(conn, CalendarError(f"Couldn't reach the calendar: {type(exc).__name__}"))
        return {"error": str(exc)}
    except CalendarError as exc:
        _fail(conn, exc)
        return {"error": str(exc)}
    conn.refresh_from_db()
    _ok(conn)
    return {**result, "pushed": pushed}


def disconnect(conn):
    """Lifts the slot blocks the calendar made, removes our upcoming events
    from it (best effort), revokes access and forgets the tokens."""
    Slot.objects.filter(calendar_block__connection=conn).update(is_blocked=False, blocked_reason="", calendar_block=None)
    provider = provider_for(conn)
    try:
        token = access_token(conn)
        upcoming = CalendarAppointmentEvent.objects.filter(connection=conn, appointment__slot__date__gte=timezone.localdate())
        for link in upcoming[:500]:
            provider.delete_event(conn, token, link.external_event_id)
        provider.revoke(conn)
    except (CalendarError, requests.RequestException) as exc:
        logger.info("Calendar %s: clean-up on disconnect incomplete: %s", conn.pk, exc)
    conn.delete()
