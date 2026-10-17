# Postman collection

`Hospital-CRM-ERP.postman_collection.json` covers every `/api/v1/` endpoint: one folder per module, one sub-folder per resource, plus a **Workflows** folder of ready-to-run request templates.

Every request also comes with:
- **A real example response.** It was recorded against the demo hospital (`product-documentation/demo-data`) while logged in as the hospital owner. Open a request's **Examples** dropdown to see it.
- **Prefilled values.** Path IDs, filters and request bodies hold real demo records, so with the **HMS – Demo hospital** environment most requests work straight away.
- **Who can use it.** The end of each description lists the roles that may call it (✅) and the roles that get `403 Forbidden` (⛔). A second example, **Denied — 403**, shows the real response a denied role receives.

## Quick start with the demo hospital

1. Start the demo backend: `product-documentation/demo-data/start_demo.ps1`. It serves `http://localhost:8010`.
2. Import the collection and `environments/HMS-Demo.postman_environment.json`, then select **HMS – Demo hospital (localhost:8010)**.
3. Run **00. Auth › Login**. The environment already holds the demo owner's login.
4. To act as another role, run a request in **00b. Log in as a role**, then any other request. For example, log in as **Front Office** and open **25. ERP - Finance › JournalEntry › List JournalEntry**: you get `403`. `current_role` shows who is logged in.

All demo users share the password in `demo_password` (a secret variable). The demo data is fictional.

## Getting started

1. Import the collection and the environment files in `environments/`:
   | Environment | `base_url` |
   |---|---|
   | HMS – Local | `http://localhost:8000` |
   | HMS – LAN dev server | `http://192.168.1.105:8000` |
   | HMS – Production | `https://app.hms.polynexus.in` |
2. Pick an environment and fill in `login_email` and `login_password`. The Local environment has the seeded development login pre-filled; the others are blank.
3. Run **00. Auth › Login (obtain access + refresh token)**. It stores `access_token` in the active environment, and every staff request sends it as a Bearer token. When it expires (after 1 hour), run **Refresh access token**.

Passwords, tokens and device keys are marked as **secret** variables in the environments.

## Variables

| Variable | What it's for |
|---|---|
| `base_url` | API host, without `/api/v1` |
| `tenant` | Hospital slug. When set, every request sends it as `X-Tenant`. Needed for public and patient-portal requests when you aren't calling the hospital's own subdomain. |
| `access_token`, `refresh_token` | Set by Login |
| `<resource>_id` (e.g. `employee_id`, `lab_order_id`) | The record that Retrieve, Update, Delete and that resource's actions work on. The demo environment prefills each one with a real record. **Create …** overwrites it with the record it just made. |
| `id` | The last record created by any **Create …** request |
| `patient_id`, `doctor_id`, `admission_id`, `bill_id`, `template_id`, `tariff_id`, `payout_id` | IDs used by the workflows and filters |
| `period_start`, `period_end` | Report and payout period. If left blank, the Workflows folder fills in the first of this month to today. |
| `portal_hospital`, `portal_mobile`, `portal_token` | Patient portal. **Verify OTP** stores `portal_token`. |
| `analyzer_token`, `device_token` | Sent as `X-Analyzer-Token` / `X-Device-Token` by the lab-analyser and bedside/ambulance device endpoints |

Every **Create …** request saves the new record's ID into `id` and a resource-specific variable, e.g. `bed_charge_rule_id`. So Create → Retrieve → Update works without copying IDs by hand. Scripts write to the active environment as well as the collection, because an environment value takes precedence over a collection value of the same name.

Optional filters are included on list and report requests as **disabled** query parameters. Tick the ones you need.

## Workflows (request templates)

**00a. Workflows (run in order)** contains chained templates for the main flows. Run a sub-folder with the Collection Runner after logging in; each step saves the IDs the next step needs.

1. **Pick records to work with:** fills `patient_id`, `doctor_id` and `admission_id` from your data.
2. **Clinical templates → specialty assessment:** installs the specialty library, builds a custom template, then records an assessment against it.
3. **Bed / room-rent billing:** creates a tariff and a charge rule, sets the policy, previews the charges, posts them, then makes an interim bill.
4. **Doctor payouts:** creates a tariff and a payout rule, finalises the bill, bills a service with its doctor, previews, generates, approves and pays a statement.
5. **Reports:** consultation time and NABH KPIs.
6. **Patient portal:** OTP login, then the patient's profiles and reports. It needs a real OTP.

Folders 1–5 are run against the API by `backend/apps/integrations/test_postman_workflows.py`, so a change that breaks a template fails the test suite. The folder is regenerated on every sync, so to customise a flow, duplicate it in Postman first.

## Requests that don't use the staff login

| Where | How it authenticates |
|---|---|
| 39. Patient Portal | **Post Verify OTP** stores `portal_token`. Portal requests send `Authorization: Portal {{portal_token}}`. |
| 37. Queue & Token Display › Public Queue Board | The display key in the URL (`{{display_key}}`) |
| 38. Telemedicine › Patient Join | The signed join token (`{{join_token}}`) |
| Lab analyser results, bedside and ambulance device feeds | `X-Analyzer-Token` / `X-Device-Token` |

## Role access

Access is checked with one demo user per role template:

| Role | Demo user |
|---|---|
| Hospital Owner / Admin | owner@demo-hospital.example |
| Front Desk Officer | frontdesk@demo-hospital.example |
| Front Office | pooja.shinde@demo.polynexus.in |
| Doctor | doctor@demo-hospital.example |
| Surgeon | dr.vikram.patil@demo.polynexus.in |
| Anaesthetist | dr.shweta.kapoor@demo.polynexus.in |
| Nurse | neha.bhosale@demo.polynexus.in |
| OT Manager | hemant.rao@demo.polynexus.in |
| Lab Technician | ganesh.pawar@demo.polynexus.in |
| Radiology Technician | yash.deshmukh@demo.polynexus.in |
| Pharmacist | manoj.kale@demo.polynexus.in |
| Billing Executive | rekha.jadhav@demo.polynexus.in |
| Finance Manager | sanjay.pandey@demo.polynexus.in |
| HR Manager | ketaki.joshi@demo.polynexus.in |
| Medical Records Officer | mrunal.apte@demo.polynexus.in |
| CRM Manager | riya.sharma@demo.polynexus.in |
| Patient Relations Executive | kunal.verma@demo.polynexus.in |
| Call Centre Executive | abhishek.malhotra@demo.polynexus.in |
| Telephony Operator | operator@demo-hospital.example |
| Marketing Manager | pranav.iyer@demo.polynexus.in |

The results reflect the **default role templates**. A hospital that edits its roles in **Settings › Roles & permissions** changes who is allowed. The full table, one row per request, is in `role-access-matrix.json`.

"Allowed" means the role got past the permission check. The example can still be a `400` when the record isn't in the right state, e.g. *"The bill isn't on hold."* Those messages are the API's own business rules, which makes them useful examples too.

## Regenerating examples and role access

From `Backend/hospital_backend`, with the demo backend **stopped**:

```
PYTHONPATH=../../product-documentation/automation DJANGO_SETTINGS_MODULE=demo_settings \
    python docs/postman/enrich_collection.py            # whole collection, about 15 minutes
python docs/postman/enrich_collection.py --only "26. ERP - HR"   # one module
```

The script runs every request in-process against the demo database. Each call is inside a transaction that is rolled back, and outbound network is blocked, so nothing is saved and no SMS, e-mail or payment call leaves the machine. Where the demo hospital has no record of a resource, the example first creates one inside the same rolled-back transaction. Any request that makes the server fail (HTTP 500) is listed in `.postman-crashes.json`. That list should be empty.

## Keeping it up to date

After adding or changing endpoints, run this from the repo root with the backend's virtualenv:

```
python docs/postman/sync_collection.py --dry-run   # show what would change
python docs/postman/sync_collection.py --prune     # apply
```

`sync_collection.py` reads Django's URL configuration and adds any endpoint the collection is missing, in the same naming style:
- Request bodies are built from the view's serializer, or from the fields the view reads off `request.data`.
- Query parameters and headers come from what the view reads.
- Descriptions come from the view's docstring.

`collection_extras.py` then:
- defines every variable the collection uses;
- adds ID capture to new **Create** requests;
- adds the tenant header script;
- regenerates the Workflows folder;
- rewrites the environment files. Environment IDs stay the same, so re-importing updates your existing environments instead of duplicating them.

Existing requests are never rewritten, with two exceptions: a URL written without its `/api/v1` prefix is corrected, and with `--prune` a request whose URL matches no route is removed. Without `--prune` such requests are only reported.
