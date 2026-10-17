"""Adds real example responses and role-access results to the Postman collection.

Every request is executed in-process against the documentation demo hospital
(product-documentation/demo-data, never a live database), each inside a
transaction that is rolled back, so nothing it does is kept:

* as the hospital owner — saved as the request's example response, and the
  request's path variables and body are prefilled with real demo values;
* as one user of each role template — HTTP 401/403 means the role may not use
  it, anything else means it may (validation errors still mean "allowed").
  The result is written into the request description ("Who can use this")
  and a real 403 response from a denied role is saved as a second example.

Also writes docs/postman/environments/HMS-Demo.postman_environment.json with
the demo IDs, and a "00b. Log in as a role" folder.

Usage (from Backend/hospital_backend, demo backend stopped):
    PYTHONPATH=../../product-documentation/automation DJANGO_SETTINGS_MODULE=demo_settings \\
        python docs/postman/enrich_collection.py [--limit N] [--only "folder name"] [--no-roles]
"""
import argparse
import copy
import json
import re
import sys
import time
import uuid
from pathlib import Path

import socket

import django

HERE = Path(__file__).resolve().parent

# No real SMS / WhatsApp / e-mail / payment / ABDM calls while generating examples.
_connect = socket.socket.connect


def _local_only(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"outbound network blocked while generating examples ({host})")
    return _connect(self, address)


socket.socket.connect = _local_only
socket.create_connection = lambda address, *a, **k: (_ for _ in ()).throw(
    ConnectionRefusedError(f"outbound network blocked while generating examples ({address[0]})"))
sys.path.insert(0, str(HERE.parents[1]))
django.setup()

from django.db import transaction  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from apps.accounts.models import User  # noqa: E402

import logging  # noqa: E402

for _name in ("django.request", "django.server", "apps", "celery"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

COLLECTION = HERE / "Hospital-CRM-ERP.postman_collection.json"
ENV_FILE = HERE / "environments" / "HMS-Demo.postman_environment.json"
DEMO_PASSWORD = "Demo@Polynexus2026"
HOSPITAL = "demo-hospital"
OWNER = "owner@demo-hospital.example"

# One demo user per role template, in the order shown in descriptions.
ROLES = [
    ("owner", "Hospital Owner / Admin", OWNER),
    ("front_desk", "Front Desk Officer", "frontdesk@demo-hospital.example"),
    ("receptionist", "Front Office", "pooja.shinde@demo.polynexus.in"),
    ("doctor", "Doctor", "doctor@demo-hospital.example"),
    ("surgeon", "Surgeon", "dr.vikram.patil@demo.polynexus.in"),
    ("anaesthetist", "Anaesthetist", "dr.shweta.kapoor@demo.polynexus.in"),
    ("nurse", "Nurse", "neha.bhosale@demo.polynexus.in"),
    ("ot_manager", "OT Manager", "hemant.rao@demo.polynexus.in"),
    ("lab_technician", "Lab Technician", "ganesh.pawar@demo.polynexus.in"),
    ("radiology_technician", "Radiology Technician", "yash.deshmukh@demo.polynexus.in"),
    ("pharmacist", "Pharmacist", "manoj.kale@demo.polynexus.in"),
    ("billing_executive", "Billing Executive", "rekha.jadhav@demo.polynexus.in"),
    ("finance_manager", "Finance Manager", "sanjay.pandey@demo.polynexus.in"),
    ("hr_manager", "HR Manager", "ketaki.joshi@demo.polynexus.in"),
    ("him_officer", "Medical Records Officer", "mrunal.apte@demo.polynexus.in"),
    ("crm_manager", "CRM Manager", "riya.sharma@demo.polynexus.in"),
    ("crm_executive", "Patient Relations Executive", "kunal.verma@demo.polynexus.in"),
    ("call_centre_executive", "Call Centre Executive", "abhishek.malhotra@demo.polynexus.in"),
    ("telephony_operator", "Telephony Operator", "operator@demo-hospital.example"),
    ("marketing_manager", "Marketing Manager", "pranav.iyer@demo.polynexus.in"),
]
SKIP_FOLDERS = ("00a. Workflows",)
NEW_ID = "999999001"  # stands in for the id of a record created just for the example  # chained templates; run them with the Collection Runner
MAX_ITEMS = 2
MAX_STR = 240
MAX_BODY = 7000
VAR = re.compile(r"\{\{(\w+)\}\}")


class Rollback(Exception):
    pass


class _Failed:
    status_code = 500

    def get(self, key, default=None):
        return default


class SafeClient(APIClient):
    """Helper look-ups (first record, current values) never stop the run; the request itself records crashes."""

    def get(self, *args, **kwargs):
        try:
            with transaction.atomic():
                resp = super().get(*args, **kwargs)
                raise Rollback
        except Rollback:
            return resp
        except Exception:  # noqa: BLE001
            return _Failed()


def walk(items, path=()):
    for i in items:
        if "item" in i:
            yield from walk(i["item"], path + (i["name"],))
        else:
            yield path, i


def trim(data, depth=0):
    """Keeps examples readable: first items of lists, short strings."""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k == "results" and isinstance(v, list):
                out[k] = [trim(x, depth + 1) for x in v[:MAX_ITEMS]]
            else:
                out[k] = trim(v, depth + 1)
        return out
    if isinstance(data, list):
        items = [trim(x, depth + 1) for x in data[:MAX_ITEMS if depth else MAX_ITEMS + 1]]
        if len(data) > len(items):
            items.append(f"… {len(data) - len(items)} more")
        return items
    if isinstance(data, str) and len(data) > MAX_STR:
        return data[:MAX_STR] + "…"
    return data


def body_text(resp):
    ctype = resp.get("Content-Type", "")
    if "json" in ctype:
        try:
            text = json.dumps(trim(resp.json()), indent=2, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            text = resp.content[:MAX_BODY].decode("utf8", "replace")
        if len(text) > MAX_BODY:
            text = text[:MAX_BODY] + "\n…(truncated)"
        return text, "json"
    if ctype.startswith("text/") and "html" not in ctype:
        return resp.content[:2000].decode("utf8", "replace"), "text"
    size = len(getattr(resp, "content", b"") or b"") if not getattr(resp, "streaming", False) else 0
    return f"<{ctype or 'binary'} file, {size:,} bytes — open in Postman with “Send and Download”>", "text"


STATUS_TEXT = {200: "OK", 201: "Created", 202: "Accepted", 204: "No Content", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
               404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 415: "Unsupported Media Type", 500: "Internal Server Error"}


class Runner:
    def __init__(self):
        from apps.core.models import Hospital

        self.hospital = Hospital.objects.get(slug=HOSPITAL)
        self.users = {}
        for key, label, email in ROLES:
            u = User.objects.filter(email=email, is_active=True).first()
            if u:
                self.users[key] = (label, u)
        self.vars = {}
        self.list_ids = {}
        self.crashes = []
        self.crash_keys = set()
        self.setups = {}  # list path → create body that worked, for resources with no demo records
        self.list_paths = set()

    # ---- variables ---------------------------------------------------------------
    def demo_variables(self, collection_vars):
        from apps.appointments.models import Doctor
        from apps.billing.models import Bill
        from apps.ipd.models import Admission
        from apps.patients.models import Patient

        h = self.hospital
        v = {k: str(val) for k, val in collection_vars.items()}
        adm = Admission.objects.filter(hospital=h, status="admitted").order_by("pk").first()
        v.update({
            "base_url": "http://localhost:8010", "tenant": HOSPITAL, "login_email": OWNER, "login_password": DEMO_PASSWORD,
            "patient_id": "121" if Patient.objects.filter(pk=121, hospital=h).exists() else str(Patient.objects.filter(hospital=h).first().pk),
            "doctor_id": str(Doctor.objects.filter(hospital=h).order_by("pk").first().pk),
            "admission_id": str(adm.pk if adm else ""), "bill_id": str(Bill.objects.filter(hospital=h).order_by("-pk").first().pk),
            "period_start": "2026-10-01", "period_end": "2026-10-15", "portal_hospital": HOSPITAL,
        })
        v["patient_mobile"] = Patient.objects.get(pk=v["patient_id"]).mobile
        self.vars = v
        return v

    def sub(self, text, extra=None):
        values = {**self.vars, **(extra or {})}
        return VAR.sub(lambda m: values.get(m.group(1), m.group(0)), text)

    def first_id(self, client, list_path):
        if list_path in self.list_ids:
            return self.list_ids[list_path]
        found = ""
        try:
            r = client.get(list_path, HTTP_X_TENANT=HOSPITAL)
            if r.status_code == 200 and "json" in r.get("Content-Type", ""):
                data = r.json()
                rows = data.get("results") if isinstance(data, dict) else data
                if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "id" in rows[0]:
                    found = str(rows[0]["id"])
        except Exception:  # noqa: BLE001
            pass
        self.list_ids[list_path] = found
        return found

    def fk_id(self, key, request_path):
        """Real id for a body field like "vendor" / "admitting_doctor" / "po_item", from the matching list endpoint."""
        app = (request_path.split("/api/v1/")[-1].split("/") or [""])[0]
        names = [key, key.split("_")[-1], key.replace("_", "-")] if "_" in key else [key]
        for name in names:
            forms = {name, name + "s", name + "es", name.replace("_", "-") + "s", (name[:-1] + "ies") if name.endswith("y") else name}
            hits = sorted((p for p in self.list_paths if p.rstrip("/").split("/")[-1] in forms), key=lambda p: (f"/{app}/" not in p, len(p)))
            for hit in hits[:3]:
                found = self.first_id(self.owner_client, hit)
                if found:
                    return int(found) if found.isdigit() else found
        return None

    def index_creates(self, collection):
        """List path → the variable its 'Create …' request saves the new id into (see collection_extras)."""
        from collection_extras import _snake

        self.create_vars, self.var_paths, self.id_vars = {}, {}, {}
        for _path, it in walk(collection["item"]):
            req = it["request"]
            raw_any = (req["url"]["raw"] if isinstance(req["url"], dict) else req["url"]).replace("{{base_url}}", "").split("?")[0]
            if req["method"] == "GET" and "{{" not in raw_any and raw_any.endswith("/"):
                self.list_paths.add(raw_any)
            if it["name"].startswith("Create ") and req["method"] == "POST":
                raw = (req["url"]["raw"] if isinstance(req["url"], dict) else req["url"]).replace("{{base_url}}", "").split("?")[0]
                var = _snake(it["name"].removeprefix("Create ").strip()).replace(" ", "_") + "_id"
                if var not in self.var_paths:
                    self.create_vars.setdefault(raw, var)
                    self.var_paths[var] = raw

    def id_variable(self, list_path):
        var = self.create_vars.get(list_path)
        if var:
            return var
        segs = [x for x in list_path.strip("/").split("/") if x and x not in ("api", "v1") and "{{" not in x]
        for n in range(1, len(segs) + 1):
            var = "_".join(segs[-n:]).replace("-", "_") + "_id"
            if self.var_paths.setdefault(var, list_path) == list_path:
                self.create_vars[list_path] = var
                return var
        return "id"

    # ---- execution ------------------------------------------------------------------
    def call(self, user, method, path, body, mode, setup=None):
        """setup=(list path, body): first create a record as the owner in the same rolled-back
        transaction and use its id for NEW_ID — for resources the demo hospital has none of."""
        client = APIClient()
        if user is not None:
            client.force_authenticate(user)
        kwargs = {"HTTP_X_TENANT": HOSPITAL}
        fn = getattr(client, method.lower())
        result = {}
        try:
            with transaction.atomic():
                if setup:
                    maker = APIClient()
                    maker.force_authenticate(self.owner)
                    made = maker.post(setup[0], setup[1], format="json", **kwargs)
                    new_id = made.json().get("id") if made.status_code in (200, 201) and "json" in made.get("Content-Type", "") else None
                    path = path.replace(NEW_ID, str(new_id or 0))
                if method in ("GET", "DELETE", "HEAD") and not body:
                    resp = fn(path, **kwargs)
                elif mode == "formdata":
                    resp = fn(path, body or {}, format="multipart", **kwargs)
                else:
                    resp = fn(path, body if body is not None else {}, format="json", **kwargs)
                result["resp"] = resp
                raise Rollback
        except Rollback:
            pass
        except Exception as exc:  # noqa: BLE001
            import traceback

            result["error"] = f"{type(exc).__name__}: {exc}"[:300]
            tb = traceback.extract_tb(exc.__traceback__)
            app_frames = [f for f in tb if "apps" in f.filename and "site-packages" not in f.filename]
            where = app_frames[-1] if app_frames else tb[-1]
            key = (method, path.split("?")[0], result["error"])
            if key not in self.crash_keys:
                self.crash_keys.add(key)
                self.crashes.append({"method": method, "path": path, "role": getattr(user, "email", "anonymous"), "error": result["error"],
                                     "at": f"{where.filename.split('hospital_backend')[-1]}:{where.lineno} {where.name}"})
        return result


def request_path(req, runner, owner_client):
    raw = req["url"]["raw"] if isinstance(req["url"], dict) else req["url"]
    url = raw.replace("{{base_url}}", "")
    path, _, query = url.partition("?")
    if "{{id}}" in path:
        list_path = path.split("{{id}}")[0]
        rid = runner.first_id(owner_client, runner.sub(list_path))
        var = runner.id_variable(list_path)
        if rid:
            runner.vars.setdefault(var, rid)
            runner.id_vars[var] = runner.vars[var]
        if not rid and list_path in runner.setups:
            path = path.replace("{{id}}", NEW_ID)
        path = path.replace("{{id}}", runner.vars.get(var) or "1")
        if isinstance(req["url"], dict):  # {{id}} → the resource's own variable, prefilled with a real demo record
            req["url"]["raw"] = req["url"]["raw"].replace("{{id}}", "{{" + var + "}}")
            req["url"]["path"] = ["{{" + var + "}}" if seg == "{{id}}" else seg for seg in req["url"].get("path", [])]
    # enabled query params only
    q = []
    if isinstance(req["url"], dict):
        for p in req["url"].get("query", []) or []:
            if not p.get("disabled"):
                q.append(f"{p['key']}={p.get('value', '')}")
    path = runner.sub(path)
    return path + ("?" + runner.sub("&".join(q)) if q else ""), path


def prefill_body(req, runner, owner_client, path_only):
    """JSON body with variables filled; for PUT/PATCH, current values of the record for the same keys."""
    b = req.get("body") or {}
    mode = b.get("mode")
    if mode == "raw":
        text = runner.sub(b.get("raw") or "")
        try:
            body = json.loads(text) if text.strip() else None
        except ValueError:
            return None, "raw", None
        if req["method"] in ("PUT", "PATCH") and isinstance(body, dict) and NEW_ID in path_only:
            made = runner.setups.get(path_only.split(NEW_ID)[0], {})
            for k in list(body):
                if k in made:
                    body[k] = made[k]
        elif req["method"] in ("PUT", "PATCH") and isinstance(body, dict):
            r = owner_client.get(path_only, HTTP_X_TENANT=HOSPITAL)
            if r.status_code == 200 and "json" in r.get("Content-Type", ""):
                current = r.json()
                if isinstance(current, dict):
                    for k in list(body):
                        if k in current and not isinstance(current[k], (dict, list)) or (k in current and isinstance(current[k], list) and all(not isinstance(x, dict) for x in current[k])):
                            body[k] = current[k]
        if isinstance(body, dict):
            for k, v in list(body.items()):
                if isinstance(v, str) and v.startswith("Example ") and k not in ("name", "title", "notes", "description", "reason", "remarks", "comment"):
                    real = runner.fk_id(k, path_only)
                    if real is not None:
                        body[k] = real
        return body, "json", json.dumps(body, indent=2, ensure_ascii=False, default=str) if body is not None else None
    if mode == "formdata":
        data = {}
        for f in b.get("formdata", []):
            if f.get("type") == "file" or f.get("disabled"):
                continue
            data[f["key"]] = runner.sub(str(f.get("value", "")))
        return data, "formdata", None
    return None, None, None


def writable_fields(path):
    """Writable serializer fields of a router 'create' endpoint, or None if the path isn't one."""
    from django.urls import resolve

    try:
        match = resolve(path.split("?")[0])
    except Exception:  # noqa: BLE001
        return None
    actions = getattr(match.func, "actions", None) or {}
    if actions.get("post") != "create":
        return None
    cls = match.func.cls
    try:
        view = cls(**getattr(match.func, "initkwargs", {}))
        view.action, view.request, view.format_kwarg, view.kwargs = "create", None, None, {}
        fields = view.get_serializer_class()().fields
    except Exception:  # noqa: BLE001
        return None
    return {k: f for k, f in fields.items() if not f.read_only and k not in ("hospital", "id")}


def related_ids(field, hospital):
    """Ids a related field accepts, within the demo hospital."""
    from rest_framework import serializers as rs

    rel = field.child_relation if isinstance(field, rs.ManyRelatedField) else field
    qs = getattr(rel, "queryset", None)
    if qs is None:
        return []
    model = qs.model
    try:
        if any(f.name == "hospital" for f in model._meta.fields):
            qs = qs.filter(hospital=hospital)
        return [pk if isinstance(pk, int) else str(pk) for pk in qs.order_by("-pk").values_list("pk", flat=True)[:25]]
    except Exception:  # noqa: BLE001
        return []


def synth(name, field, hospital, n=0):
    """A value of the right type and size for a serializer field."""
    from rest_framework import serializers as rs

    label = str(field.label or name.replace("_", " ")).strip().capitalize()
    if isinstance(field, (rs.FileField, rs.ImageField)):
        return None
    if isinstance(field, rs.ManyRelatedField):
        ids = related_ids(field, hospital)
        return ids[n:n + 1] if ids else []
    if isinstance(field, rs.RelatedField):
        ids = related_ids(field, hospital)
        return ids[n % len(ids)] if ids else None
    if isinstance(field, rs.ChoiceField) and field.choices:
        keys = list(field.choices)
        return keys[n % len(keys)]
    if isinstance(field, rs.BooleanField):
        return False
    if isinstance(field, rs.DateTimeField):
        return f"2026-10-{20 + n % 8}T10:00:00+05:30"
    if isinstance(field, rs.DateField):
        return f"2026-10-{20 + n % 8}"
    if isinstance(field, rs.TimeField):
        return "10:00:00"
    if isinstance(field, rs.DecimalField):
        low = field.min_value if field.min_value is not None else 0
        return str(max(low, 10 if (field.max_digits or 6) - (field.decimal_places or 0) >= 2 else 1))
    if isinstance(field, (rs.IntegerField, rs.FloatField)):
        low = field.min_value if field.min_value is not None else 1
        high = field.max_value if field.max_value is not None else low + 10
        return min(max(low, 1 + n), high)
    if isinstance(field, (rs.JSONField, rs.DictField)):
        return {}
    if isinstance(field, rs.ListField):
        return []
    if isinstance(field, rs.EmailField):
        return f"demo{n or ''}@demo-hospital.example"
    if isinstance(field, rs.URLField):
        return "https://demo-hospital.example/"
    if isinstance(field, rs.CharField):
        if "mobile" in name or "phone" in name:
            return f"+9190000{44000 + n:05d}"
        if name in ("pan",):
            return "ABCDE1234F"
        if "gstin" in name:
            return "27ABCDE1234F1Z5"
        if name.endswith("code") or name in ("sku", "number"):
            text = f"DEMO{n + 1}"
        else:
            text = f"Demo {label.lower()}" + (f" {n + 1}" if n else "")
        limit = field.max_length or 200
        return text[:limit] if len(text) > limit else text
    return None


def synth_body(fields, hospital, template):
    body = {}
    for k, f in fields.items():
        if not f.required and not (isinstance(template, dict) and k in template):
            continue
        v = synth(k, f, hospital)
        if v is None and isinstance(template, dict) and k in template and not (isinstance(template[k], str) and template[k].startswith("Example ")):
            v = template[k]
        if v is not None:
            body[k] = v
    return body


UNIQUE_SET = re.compile(r"The fields (.+?) must make a unique set")


UNIQUE_HINTS = ("already exists", "unique", "duplicate", "already", "must make a unique set")


def strip_ids(value):
    if isinstance(value, dict):
        return {k: strip_ids(v) for k, v in value.items() if k not in ("id", "created_at", "updated_at")}
    if isinstance(value, list):
        return [strip_ids(v) for v in value]
    return value


def distinct(value, n):
    if isinstance(value, str) and value:
        return f"{value}-{n}" if len(value) < 40 else value[:-2] + f"-{n}"
    if isinstance(value, int) and not isinstance(value, bool):
        return value + 1000 + n
    return value


def create_body(runner, owner, owner_client, path, template):
    """POST body copied from the newest real record of the same resource, retried until it validates."""
    fields = writable_fields(path)
    if not fields:
        return None
    r = owner_client.get(path.split("?")[0], HTTP_X_TENANT=HOSPITAL)
    if r.status_code != 200 or "json" not in r.get("Content-Type", ""):
        return None
    data = r.json()
    rows = data.get("results") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None
    if not rows or not isinstance(rows[0], dict):
        rows = [{}]  # nothing to copy from: every value is synthesized
    record = rows[0]
    if "id" in record:
        d = owner_client.get(path.split("?")[0].rstrip("/") + f"/{record['id']}/", HTTP_X_TENANT=HOSPITAL)
        if d.status_code == 200 and "json" in d.get("Content-Type", ""):
            record = d.json()
    body = {}
    for k, f in fields.items():
        if k in record and record[k] is not None:
            v = record[k]
            if isinstance(v, dict) and "id" in v and not getattr(f, "child", None):
                v = v["id"]  # nested display of a foreign key
            body[k] = strip_ids(v)
        elif f.required or (isinstance(template, dict) and k in template):
            v = synth(k, f, runner.hospital)
            if v is not None:
                body[k] = v
    for attempt in range(1, 7):
        res = runner.call(owner, "POST", path, body, "json")
        resp = res.get("resp")
        if resp is not None and resp.status_code == 409:  # duplicate guard (e.g. patient registration): make it a new person
            for fk in ("first_name", "name", "mobile", "email", "national_id_number"):
                if isinstance(body.get(fk), str) and body[fk]:
                    v = body[fk]
                    body[fk] = (v[:-4] + f"{4400 + attempt:04d}") if fk == "mobile" else (v.replace("@", f"+{attempt}@") if fk == "email" else (["Aarav", "Ishita", "Kabir", "Meera"][attempt % 4] if fk == "first_name" else distinct(v, attempt + 1)))
            continue
        if resp is None or resp.status_code != 400:
            return body
        try:
            errors = resp.json()
        except Exception:  # noqa: BLE001
            return body
        if not isinstance(errors, dict):
            return body
        changed = False
        for k, msgs in errors.items():
            text = json.dumps(msgs).lower()
            m = UNIQUE_SET.search(json.dumps(msgs))
            if m:  # "The fields employee, date must make a unique set." → vary the last of them
                names = [x.strip() for x in m.group(1).split(",")]
                for fk in reversed(names):
                    if fk in fields and fk in body:
                        f = fields[fk]
                        new = synth(fk, f, runner.hospital, attempt) if not isinstance(body[fk], str) or "date" in fk else distinct(body[fk], attempt + 1)
                        if new is not None and new != body[fk]:
                            body[fk] = new
                            changed = True
                            break
                continue
            if k in fields and any(h in text for h in ("invalid pk", "does not exist", "not a valid choice", "no more than", "decimal places",
                                                         "greater than or equal", "less than or equal", "not a valid", "digits in total",
                                                         "this field is required", "may not be null", "may not be blank")):
                new = synth(k, fields[k], runner.hospital, attempt)
                if new is not None and new != body.get(k):
                    body[k] = new
                    changed = True
                    continue
            if k in body and any(h in text for h in UNIQUE_HINTS):
                body[k] = distinct(body[k], attempt + 1)
                changed = True
            elif k in ("non_field_errors", "detail") and any(h in text for h in UNIQUE_HINTS):
                for fk, fv in list(body.items()):
                    if isinstance(fv, str) and fk in ("code", "name", "title", "number", "slug", "label", "key", "sku", "short_name"):
                        body[fk] = distinct(fv, attempt + 1)
                        changed = True
            elif k not in body and k in fields and isinstance(template, dict) and k in template:
                body[k] = template[k]
                changed = True
            elif k in body and ("required" in text or "may not be null" in text or "blank" in text) and isinstance(template, dict) and template.get(k) not in (None, ""):
                body[k] = template[k]
                changed = True
        if not changed:
            return body
    return body


def query_value(runner, key, current):
    v = runner.vars
    known = {"patient": v.get("patient_id"), "doctor": v.get("doctor_id"), "admission": v.get("admission_id"), "bill": v.get("bill_id"),
             "period_start": v.get("period_start"), "period_end": v.get("period_end"), "from": v.get("period_start"), "to": v.get("period_end"),
             "start": v.get("period_start"), "end": v.get("period_end"), "date_from": v.get("period_start"), "date_to": v.get("period_end"),
             "date": "2026-10-16", "month": "2026-10", "year": "2026", "mobile": v.get("patient_mobile", "")}
    if known.get(key):
        return known[key]
    real = runner.fk_id(key, "") if key not in ("search", "q", "status", "kind", "output", "format") else None
    return str(real) if real is not None else current


def enable_required_query(req, runner, resp):
    """A GET that answers 400 naming a filter: switch that filter on with a real value. True if anything changed."""
    if resp is None or resp.status_code != 400 or "json" not in resp.get("Content-Type", "") or not isinstance(req["url"], dict):
        return False
    try:
        errors = resp.json()
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(errors, dict):
        return False
    named = set(errors) | {k for k in re.findall(r"\b([a-z_]+) (?:is required|query param is required)", json.dumps(errors))}
    changed = False
    query = req["url"].setdefault("query", [])
    for key in named:
        param = next((q for q in query if q["key"] == key), None)
        if param is None and key in ("patient", "doctor", "admission", "period_start", "period_end", "from", "to", "date", "month", "mobile", "grade"):
            param = {"key": key, "value": "", "disabled": True}
            query.append(param)
        if param is None or (not param.get("disabled") and param.get("value")):
            continue
        value = query_value(runner, key, param.get("value", ""))
        if value:
            param["value"] = str(value)
            param.pop("disabled", None)
            changed = True
    if changed:
        base = req["url"]["raw"].split("?")[0]
        enabled = [f"{q['key']}={q['value']}" for q in query if not q.get("disabled")]
        req["url"]["raw"] = base + ("?" + "&".join(enabled) if enabled else "")
    return changed


def example(name, req_copy, resp, text, lang):
    code = resp.status_code
    return {
        "name": name, "originalRequest": req_copy, "status": STATUS_TEXT.get(code, str(code)), "code": code,
        "_postman_previewlanguage": lang, "header": [{"key": "Content-Type", "value": resp.get("Content-Type", "application/json")}],
        "cookie": [], "body": text,
    }


def access_markdown(allowed, denied, unchecked):
    lines = ["", "---", "**Who can use this** (checked against the demo hospital's role templates):", ""]
    if allowed:
        lines.append("✅ " + ", ".join(allowed))
    if denied:
        lines.append("")
        lines.append("⛔ " + ", ".join(denied) + " — get `403 Forbidden`")
    if unchecked:
        lines.append("")
        lines.append("_Not checked: " + ", ".join(unchecked) + "_")
    return "\n".join(lines)


ACCESS_MARK = "\n---\n**Who can use this**"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--no-roles", action="store_true")
    args = ap.parse_args()

    c = json.loads(COLLECTION.read_text(encoding="utf8"))
    coll_vars = {v["key"]: v.get("value", "") for v in c.get("variable", [])}
    runner = Runner()
    demo_vars = runner.demo_variables(coll_vars)
    runner.index_creates(c)
    owner_label, owner = runner.users["owner"]
    owner_client = SafeClient()
    owner_client.force_authenticate(owner)
    runner.owner, runner.owner_client = owner, owner_client

    stats = {"done": 0, "examples": 0, "errors": 0, "denied_examples": 0}
    matrix = []
    t0 = time.time()
    with override_settings(ALLOWED_HOSTS=["*"]):
        items = [(p, i) for p, i in walk(c["item"]) if not (p and p[0].startswith(SKIP_FOLDERS))]
        if args.only:
            items = [(p, i) for p, i in items if p and args.only.lower() in p[0].lower()]
        if args.limit:
            items = items[: args.limit]
        for n, (folder, item) in enumerate(items, start=1):
            req = item["request"]
            method = req["method"]
            noauth = (req.get("auth") or {}).get("type") == "noauth"
            full, path_only = request_path(req, runner, owner_client)
            body, mode, pretty = prefill_body(req, runner, owner_client, path_only)
            if method == "POST" and not noauth and mode in ("json", None) and not (req.get("body") or {}).get("mode") == "formdata":
                better = create_body(runner, owner, owner_client, full, body)
                if better is not None:
                    body, mode = better, "json"
                    pretty = json.dumps(better, indent=2, ensure_ascii=False, default=str)
                    req["body"] = {"mode": "raw", "raw": pretty, "options": {"raw": {"language": "json"}}}
                    if not any((h.get("key") or "").lower() == "content-type" for h in req.get("header", [])):
                        req.setdefault("header", []).append({"key": "Content-Type", "value": "application/json"})
            if pretty and (req.get("body") or {}).get("mode") == "raw" and "{{" not in (req["body"].get("raw") or "")[:0]:
                req["body"]["raw"] = pretty  # prefilled with demo values
            list_path = path_only.split(NEW_ID)[0] if NEW_ID in path_only else None
            setup = (runner.sub(list_path), runner.setups[list_path]) if list_path in runner.setups else None
            res = runner.call(None if noauth else owner, method, full, body, mode, setup)
            if method == "GET" and enable_required_query(req, runner, res.get("resp")):
                full, path_only = request_path(req, runner, owner_client)
                res = runner.call(None if noauth else owner, method, full, body, mode, setup)
            if method == "POST" and mode == "json" and res.get("resp") is not None and res["resp"].status_code == 201 \
                    and writable_fields(full) and not runner.first_id(owner_client, path_only.split("?")[0]):
                runner.setups[path_only.split("?")[0]] = body  # lets Retrieve / Update / Delete show a real record
            item["response"] = [r for r in item.get("response", []) if not r.get("name", "").startswith(("Example — ", "Denied — "))]
            orig = copy.deepcopy(req)
            if "resp" in res:
                text, lang = body_text(res["resp"])
                who = "no login" if noauth else owner_label
                item["response"].insert(0, example(f"Example — {res['resp'].status_code} {STATUS_TEXT.get(res['resp'].status_code, '')} ({who})", orig, res["resp"], text, lang))
                stats["examples"] += 1
            else:
                stats["errors"] += 1
            # role access
            desc = req.get("description") or ""
            if isinstance(desc, dict):
                desc = desc.get("content", "")
            desc = desc.split(ACCESS_MARK)[0].rstrip()
            if not args.no_roles and not noauth:
                allowed, denied, unchecked, denied_resp = [], [], [], None
                for key, (label, user) in runner.users.items():
                    r = res if key == "owner" else runner.call(user, method, full, body, mode, setup)
                    if "resp" not in r:
                        unchecked.append(label)
                        continue
                    if r["resp"].status_code in (401, 403):
                        denied.append(label)
                        denied_resp = denied_resp or (label, r["resp"])
                    else:
                        allowed.append(label)
                matrix.append({"folder": " / ".join(folder), "name": item["name"], "method": method, "path": path_only, "allowed": allowed, "denied": denied})
                desc = desc + "\n" + access_markdown(allowed, denied, unchecked)
                if denied_resp:
                    label, resp = denied_resp
                    text, lang = body_text(resp)
                    item["response"].append(example(f"Denied — {resp.status_code} {STATUS_TEXT.get(resp.status_code, '')} ({label})", orig, resp, text, lang))
                    stats["denied_examples"] += 1
            elif noauth:
                desc = desc + "\n\n---\n**Who can use this:** public — no login needed (see the folder notes for any token it expects)."
            req["description"] = desc.strip()
            stats["done"] += 1
            if n % 50 == 0:
                print(f"{n}/{len(items)} requests, {time.time() - t0:.0f}s", flush=True)

    # demo collection variables (environment holds them too)
    demo_vars.update(runner.id_vars)
    have = {v["key"] for v in c.get("variable", [])}
    for k in sorted(runner.id_vars):
        if k not in have:
            c["variable"].append({"key": k, "value": runner.id_vars[k], "type": "string"})
    for v in c.get("variable", []):
        if v["key"] in demo_vars and v["key"] not in ("access_token", "refresh_token", "login_password", "base_url", "login_email"):
            v["value"] = demo_vars[v["key"]]
    add_role_logins(c)
    COLLECTION.write_text(json.dumps(c, indent=2, ensure_ascii=False, default=str), encoding="utf8")
    write_environment(demo_vars)
    if runner.crashes:
        (HERE.parents[1] / ".postman-crashes.json").write_text(json.dumps(runner.crashes, indent=1), encoding="utf8")
    (HERE / "role-access-matrix.json").write_text(json.dumps(matrix, indent=1, ensure_ascii=False), encoding="utf8")
    print(json.dumps({**stats, "seconds": round(time.time() - t0)}))


def add_role_logins(c):
    folder = {"name": "00b. Log in as a role", "description": "Each request logs in as one demo user of that role and stores `access_token`, so every "
              "other request then runs with that role's permissions. Use it to see role-based access for yourself (e.g. log in as Front Office, "
              "then try a Finance request — you get 403). Demo password for all: `{{demo_password}}`.", "item": []}
    for key, label, email in ROLES:
        folder["item"].append({
            "name": f"Log in as {label}",
            "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": [
                "const save = (k, v) => { pm.collectionVariables.set(k, v); if (pm.environment.name) pm.environment.set(k, v); };",
                "if (pm.response.code === 200) {", "    const data = pm.response.json();", "    save('access_token', data.access);",
                "    save('refresh_token', data.refresh);", f"    save('current_role', '{label}');", "}"]}}],
            "request": {"auth": {"type": "noauth"}, "method": "POST", "header": [{"key": "Content-Type", "value": "application/json"}],
                        "url": {"raw": "{{base_url}}/api/v1/auth/login/", "host": ["{{base_url}}"], "path": ["api", "v1", "auth", "login", ""]},
                        "body": {"mode": "raw", "raw": json.dumps({"email": email, "password": "{{demo_password}}"}, indent=2)},
                        "description": f"Logs in as **{label}** (`{email}`) — role template `{key}`."},
        })
    c["item"] = [i for i in c["item"] if i["name"] != folder["name"]]
    pos = next((n for n, i in enumerate(c["item"]) if i["name"].startswith("00a")), 1)
    c["item"].insert(pos, folder)
    keys = {v["key"] for v in c.get("variable", [])}
    for k, val in (("demo_password", DEMO_PASSWORD), ("current_role", "")):
        if k not in keys:
            c.setdefault("variable", []).append({"key": k, "value": val})


def write_environment(demo_vars):
    old = json.loads(ENV_FILE.read_text(encoding="utf8")) if ENV_FILE.exists() else {}
    secret = {"login_password", "demo_password", "access_token", "refresh_token", "portal_token"}
    values = [{"key": k, "value": ("" if k in ("access_token", "refresh_token") else v), "type": "secret" if k in secret else "default", "enabled": True}
              for k, v in sorted({**demo_vars, "demo_password": DEMO_PASSWORD, "current_role": ""}.items())]
    env = {"id": old.get("id") or str(uuid.uuid4()), "name": "HMS – Demo hospital (localhost:8010)", "values": values,
           "_postman_variable_scope": "environment"}
    ENV_FILE.write_text(json.dumps(env, indent=2, ensure_ascii=False), encoding="utf8")


if __name__ == "__main__":
    main()
