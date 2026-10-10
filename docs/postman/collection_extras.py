"""Collection-wide extras applied on every sync (see sync_collection.py):

* variables   — every {{placeholder}} a request uses is defined;
* ID capture  — every "Create …" request saves the new record's id (as `id`
                and `<resource>_id`) so the next request can use it;
* headers     — X-Tenant from {{tenant}} on every request (collection
                pre-request); device/analyser token headers on the
                endpoints whose views read them;
* workflows   — "00a. Workflows", ready-to-run chained request templates for
                the main end-to-end flows (regenerated each sync);
* environments — Local / LAN / Production environment files.

Scripts write to the active environment as well as the collection, because
an environment value shadows the collection value of the same name.
"""
import json
import re
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_DIR = HERE / "environments"
WORKFLOW_FOLDER = "00a. Workflows (run in order)"

# Defaults for collection variables (environments override them).
VARIABLE_DEFAULTS = {
    "base_url": "http://localhost:8000", "tenant": "", "access_token": "", "refresh_token": "",
    "login_email": "owner@example-hospital.com", "login_password": "changeme123",
    "id": "1", "patient_id": "1", "doctor_id": "1", "admission_id": "1", "bill_id": "1", "template_id": "1",
    "tariff_id": "1", "consult_tariff_id": "1", "payout_id": "1", "share_id": "1",
    "period_start": "", "period_end": "",
    "analyzer_token": "", "device_token": "", "hospital_id": "",
}

SAVE_FN = "const save = (k, v) => { pm.collectionVariables.set(k, v); if (pm.environment.name) pm.environment.set(k, v); };"

TENANT_PREREQUEST = [
    "",
    "// =====================================================================",
    "// Tenant — which hospital a request is for",
    "// =====================================================================",
    "// Staff requests resolve the hospital from the logged-in user. Public and",
    "// patient-portal requests need it from the subdomain or X-Tenant: set",
    "// {{tenant}} (the hospital slug) in your environment.",
    "const tenant = pm.variables.get('tenant');",
    "if (tenant && !pm.request.headers.has('X-Tenant')) {",
    "    pm.request.headers.add({ key: 'X-Tenant', value: tenant });",
    "}",
]

ENVIRONMENTS = {
    "Local": {"base_url": "http://localhost:8000", "login_email": "owner@example-hospital.com", "login_password": "changeme123"},
    "LAN dev server": {"base_url": "http://192.168.1.105:8000", "login_email": "", "login_password": ""},
    "Production": {"base_url": "https://app.hms.polynexus.in", "login_email": "", "login_password": ""},
}
ENV_KEYS = [
    ("base_url", "default"), ("tenant", "default"), ("login_email", "default"), ("login_password", "secret"),
    ("access_token", "secret"), ("refresh_token", "secret"), ("portal_hospital", "default"), ("portal_mobile", "default"),
    ("portal_token", "secret"), ("analyzer_token", "secret"), ("device_token", "secret"),
    ("patient_id", "default"), ("doctor_id", "default"), ("admission_id", "default"), ("id", "default"),
]

HEADER_READ = re.compile(r"request\.headers\.get\(\s*[\"'](X-[\w-]+)[\"']")
SKIP_HEADERS = {"X-Tenant", "X-Forwarded-For"}  # tenant is added collection-wide


def _snake(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def header_variable(header):
    return header.lower().removeprefix("x-").replace("-", "_")


def _walk(items, folder=None):
    for it in items:
        if "item" in it:
            yield from _walk(it["item"], it if folder is None else folder)
        else:
            yield folder, it


# ------------------------------------------------------------ scripts

def _capture_script(var):
    return [
        "if (pm.response.code === 201 || pm.response.code === 200) {",
        "    const body = pm.response.json();",
        f"    {SAVE_FN}",
        "    if (body && body.id !== undefined) {",
        "        save('id', body.id);",
        f"        save('{var}', body.id);",
        "    }",
        "}",
    ]


def ensure_capture_scripts(col):
    """'Create X' → save the new id. Also make the existing token scripts
    (login, refresh, portal OTP, ECDH) write to the active environment."""
    added = 0
    for _folder, it in _walk(col["item"]):
        req = it["request"]
        events = it.setdefault("event", []) if "event" in it or (it["name"].startswith("Create ") and req["method"] == "POST") else None
        if events is None:
            continue
        for ev in events:
            lines = ev["script"]["exec"]
            if any("pm.collectionVariables.set(" in ln for ln in lines) and not any("const save" in ln for ln in lines):
                ev["script"]["exec"] = [SAVE_FN] + [ln.replace("pm.collectionVariables.set(", "save(") for ln in lines]
        if it["name"].startswith("Create ") and req["method"] == "POST" and not any(ev["listen"] == "test" for ev in events):
            subject = it["name"].removeprefix("Create ").strip()
            events.append({"listen": "test", "script": {"type": "text/javascript", "exec": _capture_script(_snake(subject).replace(" ", "_") + "_id")}})
            added += 1
        if not events:
            del it["event"]
    return added


def ensure_collection_prerequest(col):
    events = col.setdefault("event", [])
    pre = next((e for e in events if e["listen"] == "prerequest"), None)
    if pre is None:
        pre = {"listen": "prerequest", "script": {"type": "text/javascript", "exec": []}}
        events.append(pre)
    if not any("X-Tenant" in ln for ln in pre["script"]["exec"]):
        pre["script"]["exec"] += TENANT_PREREQUEST


def ensure_headers(item, view_source):
    """Add headers the view reads (X-Analyzer-Token, X-Device-Token, …)."""
    req = item["request"]
    have = {h["key"].lower() for h in req.get("header", [])}
    added = []
    for header in dict.fromkeys(HEADER_READ.findall(view_source)):
        if header in SKIP_HEADERS or header.lower() in have:
            continue
        entry = {"key": header, "value": "{{" + header_variable(header) + "}}"}
        if header == "X-Hospital-Id":
            entry["disabled"] = True  # platform-ops only: act on another hospital
            entry["description"] = "Only for users who can act across hospitals."
        req.setdefault("header", []).append(entry)
        added.append(header)
    return added


def ensure_variables(col):
    """Define every {{placeholder}} used (Postman dynamics like $timestamp excluded)."""
    defined = {v["key"] for v in col["variable"]}
    for k, v in VARIABLE_DEFAULTS.items():
        if k not in defined:
            col["variable"].append({"key": k, "value": v, "type": "string"})
            defined.add(k)
    text = json.dumps(col["item"]) + json.dumps(col.get("event", []))
    missing = sorted({m for m in re.findall(r"\{\{([A-Za-z_][\w-]*)\}\}", text)} - defined)
    for k in missing:
        col["variable"].append({"key": k, "value": "1" if k.endswith("_id") else "", "type": "string"})
    return missing


# ------------------------------------------------------------ workflows

def _req(name, method, path, *, body=None, query=None, tests=(), capture=None, description="", prerequest=None, auth=None, headers=()):
    segments = [p for p in path.strip("/").split("/")] + [""]
    raw = "{{base_url}}/" + "/".join(segments)
    url = {"raw": raw, "host": ["{{base_url}}"], "path": segments}
    if query:
        url["query"] = [{"key": k, "value": v} for k, v in query.items()]
        url["raw"] += "?" + "&".join(f"{k}={v}" for k, v in query.items())
    header = ([{"key": "Content-Type", "value": "application/json"}] if body is not None else []) + list(headers)
    request = {"method": method, "header": header, "url": url, "description": description}
    if body is not None:
        # Id placeholders go in unquoted ("patient": {{patient_id}}) so they
        # arrive as numbers — the API's primary-key fields reject strings.
        raw = re.sub(r'"(\{\{\w+_id\}\})"', r"\1", json.dumps(body, indent=2, ensure_ascii=False))
        request["body"] = {"mode": "raw", "raw": raw, "options": {"raw": {"language": "json"}}}
    if auth:
        request["auth"] = auth
    test = [
        f"pm.test('{name}: success', () => pm.expect(pm.response.code).to.be.oneOf([200, 201]));",
    ] + list(tests)
    if capture:
        test += ["if (pm.response.code < 300) {", "    const body = pm.response.json();", f"    {SAVE_FN}"]
        test += [f"    if ({expr} !== undefined && {expr} !== null) save('{var}', {expr});" for var, expr in capture.items()]
        test += ["}"]
    events = [{"listen": "test", "script": {"type": "text/javascript", "exec": test}}]
    if prerequest:
        events.insert(0, {"listen": "prerequest", "script": {"type": "text/javascript", "exec": prerequest}})
    return {"name": name, "event": events, "request": request, "response": []}


def _folder(name, description, items):
    return {"name": name, "description": description, "item": items}


PERIOD_PREREQUEST = [
    "// Default period: this month to today (set period_start / period_end to override).",
    "const d = new Date();",
    "const iso = (x) => x.toISOString().slice(0, 10);",
    "if (!pm.variables.get('period_start')) pm.variables.set('period_start', iso(new Date(d.getFullYear(), d.getMonth(), 1)));",
    "if (!pm.variables.get('period_end')) pm.variables.set('period_end', iso(d));",
]
FIRST_RESULT = "(body.results || body)[0] && (body.results || body)[0].id"


def build_workflows():
    pick = [
        _req("Pick a patient", "GET", "/api/v1/patients/", query={"page_size": "1"}, capture={"patient_id": FIRST_RESULT},
             description="Saves the first patient as {{patient_id}}. Skip if you've set it yourself."),
        _req("Pick a doctor", "GET", "/api/v1/doctors/", query={"page_size": "1"}, capture={"doctor_id": FIRST_RESULT},
             description="Saves the first doctor as {{doctor_id}}."),
        _req("Pick a current admission", "GET", "/api/v1/ipd/admissions/", query={"status": "admitted", "page_size": "1"}, capture={"admission_id": FIRST_RESULT},
             description="Saves the first current inpatient as {{admission_id}}."),
    ]
    templates = [
        _req("Install specialty template library", "POST", "/api/v1/clinical/assessment-templates/install-library/",
             description="Adds any of the 14 pre-built specialty templates the hospital doesn't have yet. Never overwrites edits."),
        _req("Create a custom template", "POST", "/api/v1/clinical/assessment-templates/", capture={"template_id": "body.id"},
             description="A hand-built template, as the Clinical Templates builder makes. select/multiselect fields need options; keys must be unique.",
             body={"name": "Rheumatology consultation", "category": "general", "setting": "opd", "fields": [
                 {"key": "swollen_joints", "label": "Swollen joint count", "type": "number", "required": True},
                 {"key": "morning_stiffness", "label": "Morning stiffness", "type": "select", "options": ["< 30 min", "30–60 min", "> 60 min"]},
                 {"key": "joints_involved", "label": "Joints involved", "type": "multiselect", "options": ["Hands", "Wrists", "Knees", "Feet"]},
                 {"key": "das28", "label": "DAS28 score", "type": "number"},
                 {"key": "on_dmards", "label": "On DMARDs", "type": "boolean"},
             ]}),
        _req("List OPD templates", "GET", "/api/v1/clinical/assessment-templates/", query={"setting": "opd", "is_active": "true"}),
        _req("Record a specialty assessment", "POST", "/api/v1/clinical/assessments/", capture={"id": "body.id"},
             description="Answers are checked against the template: required fields, numbers, and options for pick-one / pick-several.",
             body={"patient": "{{patient_id}}", "template": "{{template_id}}", "kind": "initial", "chief_complaint": "Joint pain and swelling",
                   "data": {"swollen_joints": 6, "morning_stiffness": "> 60 min", "joints_involved": ["Hands", "Wrists"], "das28": 4.8, "on_dmards": False}}),
    ]
    bed = [
        _req("Create a room-rent tariff", "POST", "/api/v1/finance/tariff/", capture={"tariff_id": "body.id"},
             body={"code": "RR-PVT-{{$timestamp}}", "name": "Room rent – private", "department": "Room", "rate": 3500,
                   "category_rates": {"insurance": 4000, "corporate": 3800}, "hsn_sac": "9993", "gst_rate": 0}),
        _req("Create a bed charge rule", "POST", "/api/v1/billing/bed-charge-rules/",
             description="Ward rules beat bed-type rules, which beat catch-all rules. Several rules at the winning level are all charged.",
             body={"name": "Private room rent", "bed_type": "private", "tariff": "{{tariff_id}}", "is_active": True}),
        _req("Set the bed billing policy", "PUT", "/api/v1/billing/bed-billing-policy/",
             body={"cycle": "24h", "grace_hours": 2, "checkout_hour": 12, "transfer_day_rule": "higher", "auto_post": True}),
        _req("Preview bed charges", "GET", "/api/v1/billing/bills/bed-charges/{{admission_id}}/",
             tests=["pm.test('has bed days', () => pm.expect(pm.response.json().bed_days).to.be.above(0));"],
             description="Nothing is saved. `unpriced_beds` lists beds with no matching rule."),
        _req("Post bed charges to the running bill", "POST", "/api/v1/billing/bills/bed-charges/{{admission_id}}/post/", capture={"bill_id": "body.bill"},
             description="Idempotent — re-posting rebuilds the bed lines, never duplicates them."),
        _req("Interim bill", "POST", "/api/v1/billing/bills/interim/", body={"admission": "{{admission_id}}"},
             description="Posts bed-days up to now, then snapshots every charge and payment."),
    ]
    payouts = [
        _req("Create a consultation tariff", "POST", "/api/v1/finance/tariff/", capture={"consult_tariff_id": "body.id"},
             body={"code": "CONS-{{$timestamp}}", "name": "Consultation", "department": "OPD", "rate": 800, "hsn_sac": "9993", "gst_rate": 0}),
        _req("Create a payout rule", "POST", "/api/v1/finance/doctor-payout-rules/",
             description="Most specific rule wins: doctor › service › service department › patient category › doctor's department.",
             body={"name": "Consultant share — consultations", "doctor": "{{doctor_id}}", "tariff": "{{consult_tariff_id}}", "basis": "percent",
                   "value": 60, "earned_on": "billed", "is_active": True}),
        _req("Finalise the bill", "PATCH", "/api/v1/billing/bills/{{bill_id}}/", body={"status": "unpaid"},
             description="Payouts only count services on finalised bills — drafts are never paid out."),
        _req("Bill a service with its rendering doctor", "POST", "/api/v1/billing/bills/{{bill_id}}/add-item/",
             description="`doctor` is what payouts are computed from. The bill must not be a draft to count.",
             body={"tariff": "{{consult_tariff_id}}", "quantity": 1, "doctor": "{{doctor_id}}", "service_date": "{{period_end}}"}),
        _req("Preview what's due", "GET", "/api/v1/finance/doctor-payouts/preview/", query={"period_start": "{{period_start}}", "period_end": "{{period_end}}"}),
        _req("Generate draft statements", "POST", "/api/v1/finance/doctor-payouts/generate/", capture={"payout_id": "body[0] && body[0].id"},
             body={"period_start": "{{period_start}}", "period_end": "{{period_end}}", "tds_percent": 10}),
        _req("Statement lines", "GET", "/api/v1/finance/doctor-payouts/{{payout_id}}/lines/"),
        _req("Approve statement", "POST", "/api/v1/finance/doctor-payouts/{{payout_id}}/approve/"),
        _req("Mark statement paid", "POST", "/api/v1/finance/doctor-payouts/{{payout_id}}/mark-paid/",
             description="Posts the voucher: Dr professional fees, Cr bank (net) and TDS payable.",
             body={"payment_mode": "neft", "payment_reference": "UTR{{$timestamp}}", "paid_on": "{{period_end}}"}),
    ]
    reports = [
        _req("Consultation time by doctor", "GET", "/api/v1/consultation-time/", query={"start": "{{period_start}}", "end": "{{period_end}}", "short_under": "3"}),
        _req("NABH KPIs", "GET", "/api/v1/quality/kpis/", query={"start": "{{period_start}}", "end": "{{period_end}}", "kind": "nabh"}),
    ]
    portal_auth = {"type": "noauth"}
    portal = [
        _req("Request OTP", "POST", "/api/v1/portal/auth/request-otp/", auth=portal_auth, body={"hospital": "{{portal_hospital}}", "mobile": "{{portal_mobile}}"}),
        _req("Verify OTP", "POST", "/api/v1/portal/auth/verify-otp/", auth=portal_auth, capture={"portal_token": "body.token"},
             body={"hospital": "{{portal_hospital}}", "mobile": "{{portal_mobile}}", "otp": "123456"},
             description="Use the OTP the patient received by SMS."),
        _req("My profiles", "GET", "/api/v1/portal/me/", auth=portal_auth, headers=[{"key": "Authorization", "value": "Portal {{portal_token}}"}],
             capture={"patient_id": "body.patients[0] && body.patients[0].id"}),
        _req("My reports", "GET", "/api/v1/portal/reports/", auth=portal_auth, query={"patient": "{{patient_id}}"},
             headers=[{"key": "Authorization", "value": "Portal {{portal_token}}"}]),
    ]
    return {
        "name": WORKFLOW_FOLDER,
        "description": "Ready-to-run request templates for the main end-to-end flows. Run a folder with the Collection Runner "
                       "(after 00. Auth › Login); each step saves the ids the next step needs. Regenerated by sync_collection.py — "
                       "edit a copy if you want your own variant.",
        "event": [{"listen": "prerequest", "script": {"type": "text/javascript", "exec": PERIOD_PREREQUEST}}],
        "item": [
            _folder("1. Pick records to work with", "Fills {{patient_id}}, {{doctor_id}} and {{admission_id}} from your data.", pick),
            _folder("2. Clinical templates → specialty assessment", "Library install, a hand-built template, and an assessment recorded against it.", templates),
            _folder("3. Bed / room-rent billing", "Needs {{admission_id}} (step 1).", bed),
            _folder("4. Doctor payouts", "Needs {{doctor_id}} and {{bill_id}} (run folder 3 first, or set bill_id).", payouts),
            _folder("5. Reports", "", reports),
            _folder("6. Patient portal (patient's view)", "Set {{portal_hospital}} and {{portal_mobile}} first.", portal),
        ],
    }


def ensure_workflows(col):
    col["item"] = [f for f in col["item"] if f.get("name") != WORKFLOW_FOLDER]
    auth_index = next((i for i, f in enumerate(col["item"]) if f.get("name", "").startswith("00.")), -1)
    col["item"].insert(auth_index + 1, build_workflows())


# ------------------------------------------------------------ environments

def write_environments():
    ENV_DIR.mkdir(exist_ok=True)
    written = []
    for name, overrides in ENVIRONMENTS.items():
        path = ENV_DIR / f"HMS-{name.replace(' ', '-')}.postman_environment.json"
        # Keep the id stable across runs so re-importing updates, not duplicates.
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        values = []
        for key, kind in ENV_KEYS:
            default = overrides.get(key, VARIABLE_DEFAULTS.get(key, ""))
            if key in ("portal_hospital", "portal_mobile"):
                default = ""
            values.append({"key": key, "value": default, "type": kind, "enabled": True})
        env = {
            "id": old.get("id") or str(uuid.uuid4()),
            "name": f"HMS – {name}",
            "values": values,
            "_postman_variable_scope": "environment",
        }
        path.write_text(json.dumps(env, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path.name)
    return written


def apply_all(col, write_envs=True):
    ensure_collection_prerequest(col)
    captured = ensure_capture_scripts(col)
    ensure_workflows(col)
    missing = ensure_variables(col)
    envs = write_environments() if write_envs else []
    return {"capture_scripts_added": captured, "variables_added": missing, "environments": envs}
