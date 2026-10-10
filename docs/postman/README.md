# Postman collection

`Hospital-CRM-ERP.postman_collection.json` covers every `/api/v1/` endpoint: one folder per module, one sub-folder per resource, plus a **Workflows** folder of ready-to-run request templates.

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
| `id` | The record that Retrieve, Update, Delete and custom actions work on |
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
