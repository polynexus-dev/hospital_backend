"""Add every API route missing from the Postman collection.

Walks Django's URL resolver, compares each (method, path) with the requests
already in Hospital-CRM-ERP.postman_collection.json and appends the missing
ones in the collection's own style (one folder per module, one sub-folder per
resource, "List X" / "Create X" / custom-action names). Request bodies come
from the view's serializer or from the keys the view reads off request.data;
query parameters from what the view reads off request.query_params (added
disabled, so they're visible but opt-in). Existing requests are kept as they
are, except a URL written without its /api/v1 prefix is corrected; requests
whose route no longer exists are reported (removed only with --prune).

Usage (from the repo root):  python docs/postman/sync_collection.py [--dry-run] [--prune]
  --prune  also remove requests whose path matches no route (reported otherwise)
"""
import inspect
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
COLLECTION = HERE / "Hospital-CRM-ERP.postman_collection.json"
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

sys.path.insert(0, str(HERE))
import collection_extras  # noqa: E402

django.setup()

from django.urls import URLPattern, URLResolver, get_resolver, resolve  # noqa: E402
from django.urls.resolvers import RoutePattern  # noqa: E402
from rest_framework import serializers as s  # noqa: E402
from rest_framework.permissions import AllowAny  # noqa: E402

# Folders for apps that have none in the collection yet.
NEW_FOLDERS = {
    "governance": "Governance & Security",
    "clinical": "Clinical Safety & CDSS",
    "infection_control": "Infection Control",
    "quality": "Quality & NABH KPIs",
    "support_services": "Support Services (CSSD, Housekeeping, Ambulance, Equipment)",
    "queue_mgmt": "Queue & Token Display",
    "telemedicine": "Telemedicine",
    "portal": "Patient Portal",
    "mrd": "Medical Records (MRD)",
    "dietary": "Dietary & Kitchen",
    "oncology": "Oncology",
    "schemes": "Government Schemes (PM-JAY, CGHS, ECHS)",
    "cathlab": "Cath Lab",
}
# URL kwarg → collection variable.
VARIABLES = {
    "pk": "id", "id": "id", "key": "display_key", "hi_type": "hi_type", "doctor_id": "doctor_id",
    "order_id": "id", "rx_id": "id", "patient_id": "patient_id",
}
NEW_VARIABLES = {
    "portal_token": "", "portal_mobile": "9876543210", "portal_hospital": "demo", "display_key": "reception-tv",
    "join_token": "example-join-token", "hi_type": "Prescription", "doctor_id": "1", "patient_id": "1",
}
ACTION_NAMES = {
    "list": "List", "create": "Create", "retrieve": "Retrieve", "update": "Update (PUT)",
    "partial_update": "Partial Update (PATCH)", "destroy": "Delete",
}
DATA_KEY = re.compile(r"request\.data(?:\.get\(|\[)\s*[\"'](\w+)")
QUERY_KEY = re.compile(r"(?:query_params|\bqp)(?:\.get\(|\[|\.getlist\()\s*[\"'](\w+)")
GROUP = re.compile(r"\(\?P<(\w+)>")


# ---------------------------------------------------------------- routes

def _regex_to_path(regex):
    """'^doctors/(?P<pk>[^/.]+)/queue/$' → 'doctors/{pk}/queue/' (balanced-paren aware)."""
    out, found, i = [], [], 0
    while i < len(regex):
        m = GROUP.match(regex, i)
        if m:
            depth, j = 1, m.end()
            while depth:
                depth += {"(": 1, ")": -1}.get(regex[j], 0) if regex[j - 1] != "\\" else 0
                j += 1
            body = regex[m.end():j - 1]
            if re.fullmatch(r"[\w-]+(?:\|[\w-]+)+", body):
                # Fixed set of values (e.g. status/(?P<step>scheduled|performed)): use a real one.
                options = body.split("|")
                out.append(options[0])
                found.append((m.group(1), options))
            else:
                out.append("{" + m.group(1) + "}")
            i = j
            continue
        out.append(regex[i])
        i += 1
    return "".join(out).replace("^", "").replace("$", "").replace("\\", ""), found


def _pattern_str(p):
    """(path with {placeholders}, [(url kwarg, allowed values)] for fixed-choice segments)."""
    raw = str(p.pattern)
    if isinstance(p.pattern, RoutePattern):
        return re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", raw), []  # path("doctors/<int:pk>/…")
    return _regex_to_path(raw)  # router regexes


def walk(patterns, prefix="", options=()):
    for p in patterns:
        part, found = _pattern_str(p)
        if isinstance(p, URLResolver):
            yield from walk(p.url_patterns, prefix + part, (*options, *found))
        elif isinstance(p, URLPattern):
            path = prefix + part
            if "{format}" in path or not path.startswith("api/v1/"):
                continue
            yield path, p.callback, [*options, *found]


def norm(path):
    """Compare paths ignoring what the placeholders are called."""
    path = re.sub(r"\{\{[^}]+\}\}|\{[^}]+\}|<[^>]+>", "{}", path.split("?")[0])
    return "/" + path.strip("/") + "/"


# ---------------------------------------------------------------- views

def view_info(callback):
    cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    actions = getattr(callback, "actions", None)
    allowed = set(getattr(cls, "http_method_names", ()) or ()) if cls is not None else set()
    if actions:
        return cls, {m: a for m, a in actions.items() if not allowed or m in allowed}
    if cls is None:
        return None, {}
    return cls, {m: m for m in ("get", "post", "put", "patch", "delete") if hasattr(cls, m)}


def subject(cls):
    qs = getattr(cls, "queryset", None)
    if qs is not None and getattr(cls, "actions", None) is not False and hasattr(cls, "get_queryset") and hasattr(cls, "list"):
        return qs.model.__name__
    return re.sub(r"(ViewSet|APIView|View)$", "", cls.__name__) or cls.__name__


def title(action):
    return ACTION_NAMES.get(action) or action.replace("_action", "").replace("_", " ").title()


def source(obj):
    try:
        return inspect.getsource(obj)
    except (OSError, TypeError):
        return ""


def first_paragraph(doc):
    return (inspect.cleandoc(doc).split("\n\n")[0].replace("\n", " ") if doc else "").strip()


def sample(name, field=None):
    n = name.lower()
    if isinstance(field, (s.ManyRelatedField, s.ListField)) or n.endswith("_ids"):
        return [1]
    if n == "hospital" and field is None:
        return "{{portal_hospital}}"
    if isinstance(field, s.RelatedField) or n in ("patient", "doctor", "slot", "admission", "department") or n.endswith("_id"):
        return 1
    if isinstance(field, s.BooleanField) or n.startswith(("is_", "has_")) or n in ("consent", "confirm", "active"):
        return True
    if isinstance(field, s.ChoiceField) and field.choices:
        return next(iter(field.choices))
    if isinstance(field, s.DateTimeField) or n.endswith("_at"):
        return "2026-09-24T10:00:00Z"
    if isinstance(field, s.DateField) or n.endswith(("_date", "_on")) or n in ("date", "start", "end"):
        return "2026-09-24"
    if isinstance(field, s.TimeField) or n.endswith("_time"):
        return "10:00:00"
    if isinstance(field, s.DecimalField):
        return "100.00"
    if isinstance(field, (s.IntegerField, s.FloatField)) or n in ("quantity", "amount", "score", "minutes"):
        return 1
    if isinstance(field, (s.JSONField, s.DictField)) or n in ("answers", "payload", "data"):
        return {}
    if isinstance(field, s.EmailField) or "email" in n:
        return "user@example.com"
    if n in ("mobile", "phone"):
        return "{{portal_mobile}}" if n == "mobile" else "9876543210"
    if n == "otp":
        return "123456"
    if n == "password":
        return "{{login_password}}"
    if n == "hospital":
        return "{{portal_hospital}}"
    return f"Example {name.replace('_', ' ')}"


def query_sample(cls, key, code=""):
    """Filter value for ?key=: a real choice / id / boolean from the model field when there is one."""
    if key == "output":
        return "xlsx" if '"xlsx"' in code else "pdf"
    if key == "short_under":
        return "3"
    if key == "search":
        return ""
    qs = getattr(cls, "queryset", None)
    try:
        field = qs.model._meta.get_field(key.split("__")[0]) if qs is not None else None
    except Exception:
        field = None
    if field is not None:
        if field.choices:
            return str(field.choices[0][0])
        if field.is_relation:
            return "1"
        if field.get_internal_type() == "BooleanField":
            return "true"
    value = sample(key)
    return json.dumps(value) if isinstance(value, (bool, dict, list)) else str(value)


def serializer_body(cls, callback, action):
    try:
        view = cls(**getattr(callback, "initkwargs", {}))
        view.action, view.request, view.format_kwarg, view.kwargs = action, None, None, {}
        ser = view.get_serializer_class()()
    except Exception:
        ser_cls = getattr(cls, "serializer_class", None)
        try:
            ser = ser_cls() if ser_cls else None
        except Exception:
            ser = None
    if ser is None or not hasattr(ser, "fields"):
        return None
    try:
        fields = ser.fields
    except Exception:
        return None
    return {k: sample(k, f) for k, f in fields.items() if not f.read_only and k not in ("hospital", "id")}


def build_request(path, method, cls, callback, action, app, options=()):
    handler = getattr(cls, action, None) or getattr(cls, method, None)
    code = source(handler)
    is_custom = action not in ACTION_NAMES and action not in ("get", "post", "put", "patch", "delete")
    subj = subject(cls)
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", subj)  # VerifyOTPView → "Verify OTP"
    name = f"{title(action)} {subj}" if action in ACTION_NAMES or is_custom else f"{method.title()} {spaced}"

    # Placeholder names.
    def repl(m):
        key = m.group(1)
        if key == "token":
            key = "join_token" if app == "telemedicine" else ("feedback_token" if app == "feedback" else "webhook_token")
            return "{{" + key + "}}"
        return "{{" + VARIABLES.get(key, key) + "}}"

    url_path = re.sub(r"\{(\w+)\}", repl, path)
    segments = [p for p in url_path.split("/") if p] + [""]
    query = []
    keys = list(dict.fromkeys(QUERY_KEY.findall(code) + (["start", "end"] if "parse_period" in code else [])))
    if action == "list":
        keys += [f for f in (getattr(cls, "filterset_fields", None) or []) if f not in keys]
        if getattr(cls, "search_fields", None):
            keys.append("search")
    for k in keys:
        if k == "format":
            continue
        query.append({"key": k, "value": query_sample(cls, k, code), "disabled": True})

    body = None
    if method in ("post", "put", "patch"):
        body = serializer_body(cls, callback, action) if action in ("create", "update", "partial_update") else None
        if body is None:
            body = {k: sample(k) for k in dict.fromkeys(DATA_KEY.findall(code))}

    permissions = getattr(cls, "permission_classes", ()) or ()
    public = any(p is AllowAny or getattr(p, "__name__", "") == "AllowAny" for p in permissions)
    portal = app == "portal" and not public
    desc = first_paragraph(getattr(handler, "__doc__", None)) or first_paragraph(cls.__doc__)
    for kwarg, values in options:
        desc = (desc + f" URL segment '{kwarg}' is one of: {', '.join(values)} (first one filled in).").strip()
    if public:
        desc = ("Public — no staff login. " + desc).strip()
    if portal:
        desc = ("Patient portal — send 'Authorization: Portal {{portal_token}}' (run Patient Portal > Verify OTP first). " + desc).strip()

    headers = [{"key": "Content-Type", "value": "application/json"}] if body is not None else []
    if portal:
        headers.append({"key": "Authorization", "value": "Portal {{portal_token}}"})
    raw = "{{base_url}}/" + "/".join(segments)
    url = {"raw": raw, "host": ["{{base_url}}"], "path": segments}
    if query:
        url["query"] = query
        url["raw"] += "?" + "&".join(f"{q['key']}={q['value']}" for q in query if not q.get("disabled"))
        url["raw"] = url["raw"].rstrip("?")
    req = {"method": method.upper(), "header": headers, "url": url, "description": desc}
    if public or portal:
        req["auth"] = {"type": "noauth"}
    if body is not None:
        req["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
    item = {"name": name, "request": req, "response": []}
    collection_extras.ensure_headers(item, source(cls))
    if app == "portal" and path.endswith("verify-otp/"):
        item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": [
            "if (pm.response.code === 200) {",
            "    pm.collectionVariables.set('portal_token', pm.response.json().token);",
            "}",
        ]}}]
    return subj, item


# ---------------------------------------------------------------- merge

def existing_requests(items, folder=None):
    """Yields (top-level folder, parent list, request item)."""
    for it in list(items):
        if "item" in it:
            yield from existing_requests(it["item"], folder or it)
        else:
            yield folder, items, it


def app_of(callback):
    cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None) or callback
    parts = cls.__module__.split(".")
    return parts[1] if parts[0] == "apps" and len(parts) > 1 else parts[0]


def try_resolve(path):
    """Resolve a placeholder path ('/x/{}/y/'), trying an int then a UUID for each placeholder."""
    for filler in ("1", "00000000-0000-0000-0000-000000000000"):
        try:
            return resolve(path.replace("{}", filler))
        except Exception:
            continue
    return None


def set_url(req, raw_path):
    """Point a request at '{{base_url}}' + raw_path, keeping its query string."""
    url = req["url"] if isinstance(req["url"], dict) else {"raw": req["url"]}
    query = url["raw"].split("?", 1)[1] if "?" in url["raw"] else ""
    url["raw"] = "{{base_url}}" + raw_path + (f"?{query}" if query else "")
    url["host"] = ["{{base_url}}"]
    url["path"] = [p for p in raw_path.split("/") if p] + [""]
    req["url"] = url


def main(dry_run=False, prune=False):
    col = json.loads(COLLECTION.read_text(encoding="utf-8"))
    have, app_folders, stale, fixed = set(), defaultdict(Counter), [], []
    for folder, parent, it in existing_requests(col["item"]):
        if folder is not None and folder.get("name") == collection_extras.WORKFLOW_FOLDER:
            continue  # regenerated by collection_extras; never a home for new requests
        req = it["request"]
        raw = (req["url"]["raw"] if isinstance(req["url"], dict) else req["url"]).replace("{{base_url}}", "").split("?")[0]
        path = norm(raw)
        match = try_resolve(path)
        if match is None and not path.startswith("/api/v1/") and try_resolve("/api/v1" + path):
            # Written without the /api/v1 prefix the route actually lives under.
            set_url(req, "/api/v1" + raw)
            fixed.append(f"{req['method']} {raw} → /api/v1{raw}")
            path = norm("/api/v1" + raw)
            match = try_resolve(path)
        if match is None:
            stale.append(f"{req['method']} {raw}  ({folder['name'] if folder else 'root'} › {it['name']})")
            if prune:
                parent.remove(it)
            continue
        have.add((req["method"], path))
        view = getattr(match.func, "cls", None) or getattr(match.func, "view_class", None) or match.func
        collection_extras.ensure_headers(it, source(view))
        if folder is not None:
            app_folders[app_of(match.func)][folder["name"]] += 1

    folders = {f["name"]: f for f in col["item"]}
    next_no = max(int(m.group(1)) for n in folders if (m := re.match(r"(\d+)\.", n))) + 1
    added = defaultdict(list)
    for path, callback, options in walk(get_resolver().url_patterns):
        cls, methods = view_info(callback)
        if cls is None or cls.__name__ == "APIRootView":  # DRF router index pages
            continue
        for method, action in methods.items():
            if (method.upper(), norm(path)) in have:
                continue
            app = app_of(callback)
            if app_folders[app]:
                folder_name = app_folders[app].most_common(1)[0][0]
            else:
                label = NEW_FOLDERS.get(app, app.replace("_", " ").title())
                folder_name = next((n for n in folders if n.split(". ", 1)[-1] == label), None)
                if folder_name is None:
                    folder_name = f"{next_no:02d}. {label}"
                    next_no += 1
                    folders[folder_name] = {"name": folder_name, "item": []}
                    col["item"].append(folders[folder_name])
                app_folders[app][folder_name] += 1
            subj, item = build_request(path, method, cls, callback, action, app, options)
            folder = folders[folder_name]
            sub = next((x for x in folder["item"] if "item" in x and x["name"] == subj), None)
            if sub is None:
                sub = {"name": subj, "item": []}
                folder["item"].append(sub)
            sub["item"].append(item)
            have.add((method.upper(), norm(path)))
            added[folder_name].append(f"{item['request']['method']:6} /{path}")

    known = {v["key"] for v in col.get("variable", [])}
    for k, v in NEW_VARIABLES.items():
        if k not in known:
            col["variable"].append({"key": k, "value": v, "type": "string"})

    total = sum(len(v) for v in added.values())
    for f, rows in added.items():
        print(f"\n{f}  (+{len(rows)})")
        for r in rows:
            print("   ", r)
    print(f"\nAdded {total} requests in {len(added)} folders.")
    if fixed:
        print(f"\nFixed {len(fixed)} request URLs missing the /api/v1 prefix:")
        for r in fixed:
            print("   ", r)
    if stale:
        print(f"\n{len(stale)} existing requests match no route — " + ("removed:" if prune else "left untouched (re-run with --prune to remove):"))
        for r in stale:
            print("   ", r)
    extras = collection_extras.apply_all(col, write_envs=not dry_run)
    print(f"\nID-capture scripts added to {extras['capture_scripts_added']} Create requests.")
    if extras["variables_added"]:
        print("Variables defined:", ", ".join(extras["variables_added"]))
    if not dry_run:
        COLLECTION.write_text(json.dumps(col, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {COLLECTION.name} and environments: {', '.join(extras['environments'])}")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv, prune="--prune" in sys.argv)
