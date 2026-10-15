"""Lab additions for MedNet M2.22 parity.

- Calculated tests (LabTest.formula): computed from the order's other
  results whenever one is saved, then flagged against the test's reference
  and critical limits, so a calculated critical value raises the same alert.
- Report dispatch: verified reports go to the patient by email (PDF
  attached) and/or WhatsApp (signed download link), on verification after a
  short delay or in scheduled batches; a patient's reports due together go
  in one message. Every attempt is logged (LabReportDispatch).
- Order grouping: split a request into one order per department, and/or
  merge new tests into the patient's open order placed a few minutes ago.
- Worklist: one row per test across orders (the "investigation view").
"""
import ast
import logging
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from .models import LabOrder, LabReportDispatch, LabResult, LabSettings, LabTest, SampleCollection

logger = logging.getLogger(__name__)

# --- calculated tests -------------------------------------------------------

_VAR = re.compile(r"\{([A-Za-z0-9_.-]+)\}")
_FUNCS = {"min": min, "max": max, "abs": abs, "round": round, "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp}
_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)


class FormulaError(ValueError):
    pass


def formula_codes(formula):
    return {c.upper() for c in _VAR.findall(formula or "")}


def _compile(formula):
    """{CODE} -> v_CODE; returns (expression AST, {var: code})."""
    names = {}

    def sub(m):
        name = "v_" + re.sub(r"\W", "_", m.group(1).upper())
        names[name] = m.group(1).upper()
        return name

    try:
        tree = ast.parse(_VAR.sub(sub, formula), mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"Formula isn't valid: {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Call) + _OPS):
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                raise FormulaError("Only numbers are allowed in a formula.")
            if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords):
                raise FormulaError(f"Allowed functions: {', '.join(sorted(_FUNCS))}.")
            if isinstance(node, ast.Name) and node.id not in names and node.id not in _FUNCS and node.id not in ("AGE", "FEMALE"):
                raise FormulaError(f"Unknown name {node.id!r}; write other tests as {{CODE}}.")
            continue
        raise FormulaError(f"{type(node).__name__} isn't allowed in a formula.")
    return tree, names


def validate_formula(formula):
    if formula:
        _compile(formula)


def evaluate(formula, values, *, age=None, female=None):
    """values: {CODE: number}. Returns a float, or None when an input is missing."""
    tree, names = _compile(formula)
    env = dict(_FUNCS)
    for var, code in names.items():
        if values.get(code) is None:
            return None
        env[var] = float(values[code])
    if age is not None:
        env["AGE"] = float(age)
    if female is not None:
        env["FEMALE"] = 1.0 if female else 0.0
    try:
        return float(eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}, env))  # noqa: S307 - AST checked above
    except NameError:
        return None  # AGE/FEMALE used but unknown for this patient
    except (ArithmeticError, ValueError) as exc:
        raise FormulaError(str(exc)) from exc


def _fmt(x):
    return f"{Decimal(str(round(x, 2))).normalize():f}"


def _age(patient):
    dob = getattr(patient, "date_of_birth", None)
    if not dob:
        return None
    today = timezone.localdate()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def recompute_calculated(order):
    """Fills in the order's calculated tests from its other results. Returns
    the results it created or changed. Signed results are never touched."""
    from .workflow import _num, apply_flag_and_alert

    calc_tests = [t for t in order.ordered_tests.all() if t.formula]
    if not calc_tests:
        return []
    patient = order.patient
    age, female = _age(patient), (patient.gender == "female") if patient.gender in ("male", "female") else None
    changed = []
    for _ in range(3):  # a calculated test may feed another
        values = {}
        for r in order.results.select_related("lab_test").filter(needs_repeat=False).order_by("created_at"):
            if r.lab_test.code:
                values[r.lab_test.code.upper()] = _num(r.value)
        progressed = False
        for test in calc_tests:
            try:
                x = evaluate(test.formula, values, age=age, female=female)
            except FormulaError as exc:
                logger.warning("Formula for lab test %s failed on order %s: %s", test.pk, order.pk, exc)
                continue
            if x is None:
                continue
            value = _fmt(x)
            existing = order.results.filter(lab_test=test).order_by("-created_at").first()
            if existing and (existing.finalized_at or existing.value == value):
                continue
            if existing:
                LabResult.objects.filter(pk=existing.pk).update(value=value, source="calculated")
                existing.refresh_from_db()
                result = existing
            else:
                result = LabResult.objects.create(hospital_id=order.hospital_id, lab_order=order, lab_test=test, value=value, unit=test.unit,
                                                  reference_range=test.reference_range, source="calculated")
            apply_flag_and_alert(result)
            if result.flag == LabResult.Flag.CRITICAL:
                from .signals import result_critical

                result_critical.send(sender=LabResult, result=result)
            changed.append(result)
            progressed = True
        if not progressed:
            break
    return changed


# --- order grouping -----------------------------------------------------------

def group_tests(tests, split_by_department):
    if not split_by_department:
        return [list(tests)]
    groups = defaultdict(list)
    for t in tests:
        groups[t.department].append(t)
    return [groups[d] for d in sorted(groups)]


def department_of(tests):
    depts = {t.department for t in tests}
    return depts.pop() if len(depts) == 1 else ""


def merge_target(hospital_id, patient, tests, lab_settings, split):
    """The patient's open order (nothing collected yet) placed within the
    merge window, in the same department when orders are split."""
    if not lab_settings.merge_window_minutes:
        return None
    since = timezone.now() - timedelta(minutes=lab_settings.merge_window_minutes)
    qs = LabOrder.objects.filter(hospital_id=hospital_id, patient=patient, status=LabOrder.Status.ORDERED, ordered_at__gte=since,
                                 sample_collections__isnull=True)
    if split:
        qs = qs.filter(department=department_of(tests))
    return qs.order_by("-ordered_at").first()


@transaction.atomic
def place_order(*, hospital_id, user, patient, tests, fields):
    """Returns [(order, merged: bool)]."""
    lab_settings = LabSettings.for_hospital(hospital_id)
    out = []
    for group in group_tests(tests, lab_settings.split_by_department):
        target = merge_target(hospital_id, patient, group, lab_settings, lab_settings.split_by_department)
        if target is not None:
            target.ordered_tests.add(*group)
            target.department = department_of(target.ordered_tests.all())
            if fields.get("priority") in ("urgent", "stat") and target.priority == "routine":
                target.priority = fields["priority"]
            target.save(update_fields=["department", "priority"])
            out.append((target, True))
            continue
        order = LabOrder.objects.create(hospital_id=hospital_id, patient=patient, ordered_by=user, department=department_of(group), **fields)
        order.ordered_tests.set(group)
        out.append((order, False))
    return out


# --- worklist -------------------------------------------------------------------

def worklist(hospital_id, *, department="", status="", date_from=None, date_to=None, priority="", limit=500, patient=None):
    orders = (LabOrder.objects.filter(hospital_id=hospital_id).select_related("patient")
              .prefetch_related("ordered_tests", "results", "sample_collections", "outsourced_tests").order_by("-ordered_at"))
    if patient:
        orders = orders.filter(patient_id=patient)
    if date_from:
        orders = orders.filter(ordered_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(ordered_at__date__lte=date_to)
    if priority:
        orders = orders.filter(priority=priority)
    if department:
        orders = orders.filter(ordered_tests__department=department).distinct()
    now = timezone.now()
    rows = []
    for order in orders[: limit * 2]:
        samples = [s for s in order.sample_collections.all() if s.status != SampleCollection.Status.REJECTED]
        results = {}
        for r in sorted(order.results.all(), key=lambda r: r.created_at):
            results[r.lab_test_id] = r
        outsourced = {o.lab_test_id: o for o in order.outsourced_tests.all() if o.status != "cancelled"}
        for test in order.ordered_tests.all():
            if department and test.department != department:
                continue
            r = results.get(test.pk)
            if r and r.finalized_at:
                state = "verified"
            elif r:
                state = "resulted"
            elif test.pk in outsourced:
                state = "outsourced"
            elif samples:
                state = "in_process" if any(s.status in ("received", "processing") for s in samples) else "collected"
            else:
                state = "pending_collection"
            if status and state != status:
                continue
            due = order.ordered_at + timedelta(hours=test.tat_hours) if test.tat_hours else None
            rows.append({
                "order": order.pk, "order_number": order.order_number, "patient": order.patient_id, "patient_name": order.patient.full_name,
                "uhid": order.patient.uhid, "priority": order.priority, "ordered_at": order.ordered_at, "test": test.pk, "test_name": test.name,
                "test_code": test.code, "department": test.department, "status": state, "result": r.pk if r else None,
                "value": r.value if r else "", "unit": (r.unit if r else "") or test.unit, "flag": r.flag if r else "",
                "calculated": bool(test.formula), "due_at": due, "overdue": bool(due and state not in ("verified",) and now > due),
            })
            if len(rows) >= limit:
                return rows
    return rows


PROCESSING_STATES = ("not_started", "in_process", "results_ready")


def with_processing_counts(qs):
    """Annotates tests ordered and tests with a result, for the order-level
    processing filter: not started / in process / results ready."""
    from django.db.models import Count

    return qs.annotate(n_tests=Count("ordered_tests", distinct=True), n_resulted=Count("results__lab_test", distinct=True)).order_by("-ordered_at", "-pk")


def filter_processing(qs, state):
    from django.db.models import F

    qs = with_processing_counts(qs)
    if state == "not_started":
        return qs.filter(n_resulted=0)
    if state == "in_process":
        return qs.filter(n_resulted__gt=0, n_resulted__lt=F("n_tests"))
    if state == "results_ready":
        return qs.filter(n_tests__gt=0, n_resulted__gte=F("n_tests"))
    return qs


def processing_state(order):
    tests = getattr(order, "n_tests", None)
    done = getattr(order, "n_resulted", None)
    if tests is None:
        tests = order.ordered_tests.count()
        done = order.results.values("lab_test").distinct().count()
    return "not_started" if not done else "results_ready" if tests and done >= tests else "in_process"


def patient_lab_status(hospital_id, patient_id):
    """Every open investigation of the patient with its stage (colour-coded on
    screen), counts per stage, and the reporting section: verified orders
    with who finalised them and whether the report went out."""
    rows = worklist(hospital_id, patient=patient_id, limit=300)
    pending = [r for r in rows if r["status"] != "verified"]
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    reported = (LabOrder.objects.filter(hospital_id=hospital_id, patient_id=patient_id, status=LabOrder.Status.VERIFIED)
                .prefetch_related("ordered_tests", "results__finalized_by").order_by("-ordered_at")[:30])
    reporting = []
    for o in reported:
        final = max((r for r in o.results.all() if r.finalized_at), key=lambda r: r.finalized_at, default=None)
        by = final.finalized_by if final else None
        reporting.append({
            "order": o.pk, "order_number": o.order_number, "department": o.department or ", ".join(sorted({t.department for t in o.ordered_tests.all()})),
            "tests": ", ".join(t.name for t in o.ordered_tests.all()), "ordered_at": o.ordered_at, "verified_at": final.finalized_at if final else None,
            "verified_by": (by.get_full_name() or by.email) if by else "", "dispatched_at": o.dispatched_at,
            "status": "dispatched" if o.dispatched_at else "verified",
        })
    return {"pending": pending, "counts": counts, "reporting": reporting}


# --- report dispatch ---------------------------------------------------------------

LINK_SALT = "laboratory.report-link"
LINK_MAX_AGE = 30 * 24 * 3600


def report_link(order):
    token = signing.dumps({"o": order.pk, "h": str(order.hospital_id)}, salt=LINK_SALT, compress=True)
    return f"{settings.PUBLIC_APP_URL.rstrip('/')}/api/v1/laboratory/report-link/{token}/"


def order_from_token(token):
    try:
        data = signing.loads(token, salt=LINK_SALT, max_age=LINK_MAX_AGE)
    except signing.BadSignature:
        return None
    return LabOrder.objects.filter(pk=data.get("o"), hospital_id=data.get("h"), status=LabOrder.Status.VERIFIED).select_related("patient", "hospital").first()


def next_dispatch_time(lab_settings, now=None):
    now = timezone.localtime(now or timezone.now())
    times = []
    for t in lab_settings.dispatch_times or []:
        try:
            hh, mm = (int(x) for x in str(t).split(":"))
            times.append((hh, mm))
        except ValueError:
            continue
    if not times:
        return now + timedelta(minutes=lab_settings.dispatch_delay_minutes)
    for day in (0, 1):
        base = (now + timedelta(days=day)).date()
        for hh, mm in sorted(times):
            at = timezone.make_aware(datetime.combine(base, datetime.min.time()).replace(hour=hh, minute=mm))
            if at > now:
                return at
    return now


def schedule_dispatch(order):
    """Called when an order becomes verified."""
    lab_settings = LabSettings.for_hospital(order.hospital_id)
    if lab_settings.auto_dispatch and lab_settings.dispatch_channels and not order.dispatched_at:
        if not lab_settings.dispatch_outsourced and has_outsourced(order):
            return  # held for staff to review and send by hand
        LabOrder.objects.filter(pk=order.pk).update(dispatch_due_at=next_dispatch_time(lab_settings))


def has_outsourced(order):
    return order.outsourced_tests.exclude(status="cancelled").exists()


def send_reports(orders, channels, *, user=None, automatic=False):
    """Sends one patient's verified orders in one message per channel.
    Returns the LabReportDispatch rows."""
    from django.core.mail import EmailMessage

    from apps.communications.adapters import get_whatsapp_provider
    from apps.communications.services import is_opted_out

    from .workflow import render_lab_report

    orders = [o for o in orders if o.status == LabOrder.Status.VERIFIED]
    if not orders:
        return []
    patient, hospital = orders[0].patient, orders[0].hospital
    numbers = ", ".join(o.order_number for o in orders)
    rows = []

    def log(channel, recipient, error=""):
        for o in orders:
            rows.append(LabReportDispatch.objects.create(hospital_id=o.hospital_id, lab_order=o, channel=channel, recipient=recipient,
                                                         sent=not error, error=error[:255], automatic=automatic, sent_by=user))

    for channel in channels:
        if is_opted_out(patient, channel):
            log(channel, "", "The patient has opted out of this channel.")
            continue
        if channel == "email":
            if not patient.email:
                log("email", "", "No email address on the patient's record.")
                continue
            try:
                msg = EmailMessage(subject=f"Your lab report{'s' if len(orders) > 1 else ''} from {hospital.name}",
                                   body=f"Dear {patient.full_name},\n\nYour laboratory report{'s are' if len(orders) > 1 else ' is'} attached ({numbers}).\n\n{hospital.name}",
                                   from_email=settings.MESSAGING_FROM_EMAIL, to=[patient.email])
                if len(orders) > 1 and LabSettings.for_hospital(orders[0].hospital_id).merge_dispatch_reports:
                    from .merged_report import render_patient_lab_report

                    msg.attach(f"Lab_report_{patient.uhid}.pdf", render_patient_lab_report(orders), "application/pdf")
                else:
                    for o in orders:
                        msg.attach(f"Lab_report_{o.order_number}.pdf", render_lab_report(o), "application/pdf")
                for o in orders:
                    for out in o.outsourced_tests.filter(report_type="outside_report").exclude(result_file=""):
                        with out.result_file.open("rb") as fh:  # the external lab's own report goes along with ours
                            msg.attach(f"Outside_report_{o.order_number}_{out.lab_test.code or out.pk}{os.path.splitext(out.result_file.name)[1]}", fh.read())
                msg.send(fail_silently=False)
                log("email", patient.email)
            except Exception as exc:  # noqa: BLE001 - logged for staff to resend
                logger.exception("Lab report email failed for orders %s", numbers)
                log("email", patient.email, f"Email failed: {exc}")
        elif channel == "whatsapp":
            if not patient.mobile:
                log("whatsapp", "", "No mobile number on the patient's record.")
                continue
            links = "\n".join(f"{o.order_number}: {report_link(o)}" for o in orders)
            try:
                get_whatsapp_provider().send(to=patient.mobile, body=f"Dear {patient.full_name}, your lab report{'s are' if len(orders) > 1 else ' is'} ready at {hospital.name}.\n{links}")
                log("whatsapp", patient.mobile)
            except Exception as exc:  # noqa: BLE001
                log("whatsapp", patient.mobile, f"WhatsApp failed: {exc}")
    if any(r.sent for r in rows):
        LabOrder.objects.filter(pk__in=[o.pk for o in orders]).update(dispatched_at=timezone.now(), dispatch_due_at=None)
    return rows


MAX_AUTO_ATTEMPTS = 3


def dispatch_due(now=None):
    """Celery beat: send every report that's due, one message per patient."""
    now = now or timezone.now()
    due = (LabOrder.objects.filter(status=LabOrder.Status.VERIFIED, dispatched_at__isnull=True, dispatch_due_at__lte=now)
           .select_related("patient", "hospital").order_by("hospital_id", "patient_id"))
    by_patient = defaultdict(list)
    for o in due:
        by_patient[(o.hospital_id, o.patient_id)].append(o)
    sent = 0
    for (hospital_id, _), orders in by_patient.items():
        lab_settings = LabSettings.for_hospital(hospital_id)
        if not lab_settings.dispatch_outsourced:
            held = [o for o in orders if has_outsourced(o)]
            if held:  # setting turned off after scheduling: stop the automatic send
                LabOrder.objects.filter(pk__in=[o.pk for o in held]).update(dispatch_due_at=None)
            orders = [o for o in orders if o not in held]
            if not orders:
                continue
        rows = send_reports(orders, lab_settings.dispatch_channels, automatic=True)
        if any(r.sent for r in rows):
            sent += len(orders)
            continue
        for o in orders:  # all channels failed: retry later, up to 3 runs
            attempts = o.dispatches.filter(automatic=True).count() // max(len(lab_settings.dispatch_channels), 1)
            LabOrder.objects.filter(pk=o.pk).update(dispatch_due_at=None if attempts >= MAX_AUTO_ATTEMPTS else now + timedelta(minutes=30))
    return sent
