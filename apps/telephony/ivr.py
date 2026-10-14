"""Phone menu (dynamic IVR), call queue and hold menu, served to the phone
system one step at a time. The menu is built in our app (IVRMenu / IVRNode);
the PBX only plays what we return and posts back the keys pressed.

Vendor-neutral JSON protocol. Every request carries the PBX's call id:

    POST /api/v1/ivr/<webhook token>/step/   {"call_id", "caller", "digits"}
        First request (no digits yet) starts the welcome menu; later ones
        carry the key the caller pressed ("" when they pressed nothing).
    POST /api/v1/ivr/<webhook token>/hold/   {"call_id", "digits"}
        Polled while the caller waits in a queue (every `next_check_seconds`,
        and whenever they press a key).
    POST /api/v1/ivr/<webhook token>/event/  {"call_id", "event": "answered" | "hangup"}

Every reply is {"action", "say", "language", ...}:
    collect  play `say`, read one key, then call /step/ with it
    queue    play `say`, put the caller in queue `target`, then poll /hold/
    hold     play `say` to the waiting caller; poll /hold/ again later
    transfer play `say`, connect to `target`
    hangup   play `say`, end the call
    none     nothing to do (a /hold/ poll after staff answered)
`say` is text for the PBX's text-to-speech in `language` (en / hi / mr).
"""
import logging
import math
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Avg
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.encryption import normalize_phone
from apps.core.modules import require_module

from .models import Call, CallbackTask, IVRMenu, IVRNode, IVRSession

logger = logging.getLogger(__name__)

MAX_WRONG_KEYS = 3
DEFAULT_HANDLE_SECONDS = 180
STALE_SESSION_HOURS = 2

# Built-in phrases. Menu wording is the hospital's own (IVRNode.prompt);
# these are only the system messages around it.
PHRASES = {
    "en": {
        "option": "For {label}, press {digit}.",
        "wrong_key": "Sorry, that is not a valid option.",
        "connecting": "Please hold while we connect you.",
        "position": "You are number {position} in line. The expected wait is about {minutes} minutes.",
        "position_next": "You are next in line.",
        "key_callback": "Press 1 and we will call you back instead.",
        "key_info": "Press 2 for hospital information.",
        "key_booking": "Press 3 to get our booking link on WhatsApp or SMS.",
        "callback_booked": "Thank you. We will call you back on this number shortly. Goodbye.",
        "link_sent": "We have sent the booking link to your phone.",
        "link_failed": "Sorry, we could not send the link right now.",
        "goodbye": "Thank you for calling. Goodbye.",
    },
    "hi": {
        "option": "{label} के लिए {digit} दबाएँ।",
        "wrong_key": "क्षमा करें, यह सही विकल्प नहीं है।",
        "connecting": "कृपया प्रतीक्षा करें, हम आपको जोड़ रहे हैं।",
        "position": "कतार में आपका नंबर {position} है। अनुमानित प्रतीक्षा लगभग {minutes} मिनट है।",
        "position_next": "आप कतार में अगले हैं।",
        "key_callback": "प्रतीक्षा करने के बजाय कॉल-बैक के लिए 1 दबाएँ।",
        "key_info": "अस्पताल की जानकारी के लिए 2 दबाएँ।",
        "key_booking": "व्हाट्सऐप या SMS पर बुकिंग लिंक पाने के लिए 3 दबाएँ।",
        "callback_booked": "धन्यवाद। हम जल्द ही इसी नंबर पर आपको वापस कॉल करेंगे। नमस्कार।",
        "link_sent": "बुकिंग लिंक आपके फ़ोन पर भेज दिया गया है।",
        "link_failed": "क्षमा करें, अभी लिंक नहीं भेजा जा सका।",
        "goodbye": "कॉल करने के लिए धन्यवाद। नमस्कार।",
    },
    "mr": {
        "option": "{label} साठी {digit} दाबा.",
        "wrong_key": "क्षमस्व, हा योग्य पर्याय नाही.",
        "connecting": "कृपया थांबा, आम्ही तुम्हाला जोडत आहोत.",
        "position": "रांगेत तुमचा क्रमांक {position} आहे. अंदाजे प्रतीक्षा सुमारे {minutes} मिनिटे आहे.",
        "position_next": "रांगेत पुढचा क्रमांक तुमचा आहे.",
        "key_callback": "थांबण्याऐवजी आम्ही परत कॉल करावा यासाठी 1 दाबा.",
        "key_info": "रुग्णालयाच्या माहितीसाठी 2 दाबा.",
        "key_booking": "व्हॉट्सॲप किंवा SMS वर बुकिंग लिंक मिळवण्यासाठी 3 दाबा.",
        "callback_booked": "धन्यवाद. आम्ही लवकरच याच नंबरवर तुम्हाला परत कॉल करू. नमस्कार.",
        "link_sent": "बुकिंग लिंक तुमच्या फोनवर पाठवली आहे.",
        "link_failed": "क्षमस्व, आत्ता लिंक पाठवता आली नाही.",
        "goodbye": "कॉल केल्याबद्दल धन्यवाद. नमस्कार.",
    },
}


def _t(session, key, **kw):
    return PHRASES.get(session.language, PHRASES["en"])[key].format(**kw)


def _reply(session, action, say, **extra):
    return {"action": action, "say": " ".join(s for s in say if s) if isinstance(say, (list, tuple)) else say,
            "language": session.language or "en", **extra}


# --- Menu ------------------------------------------------------------------


def root_node(menu):
    return menu.nodes.filter(parent__isnull=True).first()


def _menu_prompt(session, node):
    if node.prompt.strip():
        return node.prompt.strip()
    return " ".join(_t(session, "option", label=c.label, digit=c.digit) for c in node.children.all() if c.digit)


def _present(session, node, *before):
    session.current_node = node
    session.wrong_keys = 0
    session.save(update_fields=["current_node", "wrong_keys", "language", "department", "path", "updated_at"])
    return _reply(session, "collect", [*before, _menu_prompt(session, node)], max_digits=1, timeout_seconds=6)


def _end(session, state, say):
    session.state = state
    session.ended_at = timezone.now()
    session.save(update_fields=["state", "ended_at", "updated_at"])
    return _reply(session, "hangup", say)


def _wrong_key(session):
    session.wrong_keys += 1
    if session.wrong_keys >= MAX_WRONG_KEYS:
        if session.menu.fallback_target:
            session.save(update_fields=["wrong_keys", "updated_at"])
            return _reply(session, "transfer", _t(session, "connecting"), target=session.menu.fallback_target)
        return _end(session, IVRSession.State.ENDED, _t(session, "goodbye"))
    session.save(update_fields=["wrong_keys", "updated_at"])
    return _reply(session, "collect", [_t(session, "wrong_key"), _menu_prompt(session, session.current_node)], max_digits=1, timeout_seconds=6)


def _select(session, node):
    session.path = " > ".join(p for p in (session.path, node.label) if p)[:255]
    A = IVRNode.Action
    if node.action == A.LANGUAGE:
        session.language = node.language or session.language
        return _present(session, node)
    if node.action == A.MENU:
        return _present(session, node)
    if node.action == A.QUEUE:
        return _join_queue(session, node)
    if node.action == A.INFO:
        return _present(session, session.current_node, node.prompt.strip())
    if node.action == A.CALLBACK:
        if node.department_id:
            session.department = node.department
        request_callback(session, CallbackTask.Source.IVR_MENU)
        return _end(session, IVRSession.State.CALLBACK, _t(session, "callback_booked"))
    if node.action == A.BOOKING_LINK:
        sent = send_booking_link(session)
        return _present(session, session.current_node, _t(session, "link_sent" if sent else "link_failed"))
    return _wrong_key(session)


def step(session, digits):
    if session.state == IVRSession.State.QUEUED:
        return hold(session, digits)
    if session.state != IVRSession.State.IN_MENU:
        return _reply(session, "hangup", "")
    if session.current_node_id is None:
        root = root_node(session.menu)
        if root is None:
            return _end(session, IVRSession.State.ENDED, _t(session, "goodbye"))
        if root.action != IVRNode.Action.MENU:  # a one-option menu: e.g. straight into the queue
            return _select(session, root)
        return _present(session, root)
    child = session.current_node.children.filter(digit=digits).first() if digits else None
    return _select(session, child) if child else _wrong_key(session)


# --- Queue and hold menu ---------------------------------------------------


def queue_position(session):
    ahead = IVRSession.objects.filter(
        hospital_id=session.hospital_id, state=IVRSession.State.QUEUED, is_test=session.is_test,
        queue_node_id=session.queue_node_id, queued_at__lt=session.queued_at,
    )
    return ahead.count() + 1


def average_handle_seconds(hospital_id, department_id):
    since = timezone.now() - timedelta(days=7)
    answered = Call.objects.filter(hospital_id=hospital_id, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED,
                                   started_at__gte=since, duration_seconds__gt=0)
    avg = None
    if department_id:
        avg = answered.filter(department_id=department_id).aggregate(a=Avg("duration_seconds"))["a"]
    if avg is None:
        avg = answered.aggregate(a=Avg("duration_seconds"))["a"]
    return avg or DEFAULT_HANDLE_SECONDS


def estimated_wait_seconds(session, position):
    agents = max(1, session.queue_node.queue_agents if session.queue_node_id else 1)
    return int(math.ceil((position - 1) / agents) * average_handle_seconds(session.hospital_id, session.department_id))


def _hold_message(session, *before):
    position = queue_position(session)
    wait = estimated_wait_seconds(session, position)
    keys = [_t(session, "key_callback")]
    if session.menu.hold_info_text.strip():
        keys.append(_t(session, "key_info"))
    if session.menu.booking_url:
        keys.append(_t(session, "key_booking"))
    where = _t(session, "position_next") if wait == 0 else _t(session, "position", position=position, minutes=max(1, round(wait / 60)))
    return [*before, where, *keys], position, wait


def _join_queue(session, node):
    session.state = IVRSession.State.QUEUED
    session.queue_node = node
    session.department = node.department or session.department
    session.queued_at = timezone.now()
    session.save(update_fields=["state", "queue_node", "department", "queued_at", "path", "language", "updated_at"])
    say, position, wait = _hold_message(session, _t(session, "connecting"))
    return _reply(session, "queue", say, target=node.queue_target, position=position, estimated_wait_seconds=wait,
                  next_check_seconds=session.menu.hold_announce_seconds)


def hold(session, digits=""):
    if session.state != IVRSession.State.QUEUED:
        return _reply(session, "hangup" if session.state != IVRSession.State.ANSWERED else "none", "")
    menu = session.menu
    before = []
    if digits == "1":
        request_callback(session, CallbackTask.Source.QUEUE)
        return _end(session, IVRSession.State.CALLBACK, _t(session, "callback_booked"))
    if digits == "2" and menu.hold_info_text.strip():
        before.append(menu.hold_info_text.strip())
    elif digits == "3" and menu.booking_url:
        before.append(_t(session, "link_sent" if send_booking_link(session) else "link_failed"))
    say, position, wait = _hold_message(session, *before)
    session.save(update_fields=["updated_at"])
    return _reply(session, "hold", say, position=position, estimated_wait_seconds=wait, next_check_seconds=menu.hold_announce_seconds)


# --- What a caller can ask for -------------------------------------------


def request_callback(session, source):
    """One open callback per number: a caller who asks twice, or whose call
    then shows up as missed, keeps the task they already have."""
    from apps.patients.models import Patient

    from .signals import CALLBACK_SLA_MINUTES, OPEN_CALLBACK_STATUSES

    if session.is_test:
        return None
    existing = CallbackTask.objects.filter(hospital_id=session.hospital_id, phone_number=session.caller,
                                           status__in=OPEN_CALLBACK_STATUSES).first()
    if existing is not None:
        return existing
    return CallbackTask.objects.create(
        hospital_id=session.hospital_id, phone_number=session.caller, department=session.department, source=source,
        patient=Patient.objects.filter(hospital_id=session.hospital_id).by_mobile(session.caller).first(),
        sla_due_at=timezone.now() + timedelta(minutes=CALLBACK_SLA_MINUTES),
        notes=f"Asked for a callback on the phone ({session.path})" if session.path else "Asked for a callback on the phone",
    )



def send_booking_link(session):
    """WhatsApp (SMS fallback) to a known patient, or to the caller's open
    enquiry — creating one, since the caller has just asked us to contact
    them. At most once per call."""
    from apps.clinical.notify import notify_patient
    from apps.communications.services import send_to_enquiry
    from apps.enquiries.models import Enquiry
    from apps.enquiries.services import OPEN_STAGES, find_duplicates
    from apps.patients.models import Patient

    menu = session.menu
    if not menu.booking_url or session.is_test:
        return bool(menu.booking_url) and session.is_test
    if not cache.add(f"ivr-link:{session.pk}", 1, 3600):
        return True
    hospital = session.hospital
    patient = Patient.objects.filter(hospital=hospital).by_mobile(session.caller).first()
    if patient is not None:
        message = notify_patient(patient, "booking_link", {"link": menu.booking_url})
    else:
        mobile = normalize_phone(session.caller)
        enquiry = find_duplicates(hospital, mobile=mobile).filter(stage__in=OPEN_STAGES, duplicate_of__isnull=True).first()
        if enquiry is None:
            enquiry = Enquiry.objects.create(
                hospital=hospital, name="Phone caller", mobile=mobile, source=Enquiry.Source.IVR, department=session.department,
                notes=f"Asked for the booking link on the phone ({session.path}).",
            )
        message = send_to_enquiry(enquiry=enquiry, channel="whatsapp", purpose="booking_link", context={"link": menu.booking_url},
                                  fallback_channel="sms")
    return message is not None and message.status != "failed"


# --- Webhooks --------------------------------------------------------------


def _menu(token):
    menu = IVRMenu.objects.filter(webhook_token=token, is_active=True, hospital__is_active=True).select_related("hospital").first()
    if menu is not None:
        require_module(menu.hospital, "telephony")
    return menu


def get_session(menu, call_id, caller="", *, is_test=False):
    try:
        with transaction.atomic():
            session, _ = IVRSession.objects.get_or_create(
                hospital=menu.hospital, provider_call_id=call_id,
                defaults={"menu": menu, "caller": caller[:32], "is_test": is_test},
            )
    except IntegrityError:  # the PBX retried the first request
        session = IVRSession.objects.get(hospital=menu.hospital, provider_call_id=call_id)
    return session


def apply_event(session, event):
    now = timezone.now()
    if event == "answered" and session.state in (IVRSession.State.QUEUED, IVRSession.State.IN_MENU):
        session.state, session.answered_at = IVRSession.State.ANSWERED, now
    elif event == "hangup":
        if session.state == IVRSession.State.QUEUED:
            session.state = IVRSession.State.ABANDONED
        elif session.state == IVRSession.State.IN_MENU:
            session.state = IVRSession.State.ENDED
        session.ended_at = session.ended_at or now
    session.save()


class _IVRWebhook(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, token):
        menu = _menu(token)
        if menu is None:
            return Response({"detail": "Unknown phone menu."}, status=404)
        data = request.data if isinstance(request.data, dict) else {}
        call_id = str(data.get("call_id") or data.get("callid") or "")[:128]
        if not call_id:
            return Response({"call_id": "Missing."}, status=400)
        digits = str(data.get("digits") or "").strip()[:1]
        return self.handle(menu, call_id, data, digits)


class IVRStepView(_IVRWebhook):
    def handle(self, menu, call_id, data, digits):
        session = get_session(menu, call_id, str(data.get("caller") or data.get("from_number") or ""))
        return Response(step(session, digits))


class IVRHoldView(_IVRWebhook):
    def handle(self, menu, call_id, data, digits):
        session = IVRSession.objects.filter(hospital=menu.hospital, provider_call_id=call_id).first()
        if session is None:
            return Response({"detail": "Unknown call."}, status=404)
        return Response(hold(session, digits))


class IVREventView(_IVRWebhook):
    def handle(self, menu, call_id, data, digits):
        session = IVRSession.objects.filter(hospital=menu.hospital, provider_call_id=call_id).first()
        if session is None:
            return Response({"detail": "Unknown call."}, status=404)
        event = str(data.get("event") or "")
        if event not in ("answered", "hangup"):
            return Response({"event": "Use answered or hangup."}, status=400)
        apply_event(session, event)
        return Response({"state": session.state})
