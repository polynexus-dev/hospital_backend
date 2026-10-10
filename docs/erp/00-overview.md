# Hospital ERP Expansion — Architecture Overview

**Status:** Planning — no ERP code exists yet. This document set is the pre-implementation architecture your spec asked for (module hierarchy, roles, permission matrix, workflows, integration, domain model, API/event spec, navigation, dashboards, audit model, backlog), produced *before* writing any ERP application code.

**Decision captured here:** build the ERP as new Django apps inside this same codebase (`Backend/apps/`), alongside the existing CRM apps — not as a separate product, and not by continuing to rely solely on the external-HIS adapter (`apps/integrations`). That adapter is **kept**, not replaced — see "External HIS coexistence" below.

**Document set:**
- `00-overview.md` — this file: principles and the decisions everything else depends on
- `01-module-hierarchy.md` — every module, mapped to Django apps, phased
- `02-domain-model.md` — new apps' core models and REST API conventions
- `03-rbac-and-roles.md` — full CRM+ERP role list, permission matrix, new RBAC mechanism
- `04-workflows.md` — CRM workflow and the ERP patient-lifecycle workflow (diagrams)
- `05-integration-architecture.md` — CRM↔ERP integration, now internal (domain events)
- `06-navigation-and-dashboards.md` — sidebar structure, dashboard specs
- `07-audit-and-security.md` — audit trail extension, finalize/amendment workflow
- `08-implementation-backlog.md` — the actionable phased backlog

---

## 1. Why "inside this codebase," not "two products"

Your spec's Part 11 says CRM and ERP must have separate navigation, dashboards, permissions, workflows, reporting, and API integration, with only "shared identity where necessary." That's a **logical/architectural** separation requirement, not necessarily a **deployment** one. Splitting into two separately-deployed services today would mean:

- Duplicating auth, tenancy, and RBAC (`apps.core`, `apps.accounts`) in a second service, or building a second integration layer just to keep them in sync — exactly the "uncontrolled database coupling" your spec warns against, except now over a network.
- A hospital's `Patient` identity (UHID) needing to be created in one service and synced to the other on every registration, with all the consistency problems that implies for a system handling PHI.

Instead: **one Django project, two clearly bounded sets of apps, connected by domain events, not shared tables.** CRM apps (`enquiries`, `communications`, `feedback`, `referrals`, `packages`, `automation`, `analytics`, `telephony`) and ERP apps (new: `opd`, `ipd`, `emergency`, `laboratory`, `radiology`, `pharmacy`, `ot`, `icu`, `bloodbank`, `billing`, `inventory`, `finance`, `hr`) stay logically separate — no CRM app queries an ERP app's tables directly, and vice versa. They talk through Django signals / a lightweight internal event dispatcher (see `05-integration-architecture.md`). That gets you the architectural separation your spec requires without inventing network-integration problems a single hospital's on-prem deployment (see `Hospital.is_on_premise` — this product already ships on-prem) doesn't need.

If a future requirement genuinely needs the ERP to scale/deploy independently, the event-based boundary designed here is exactly what makes that extraction possible later without a rewrite — apps that only talk through events can be split into separate services with the same event contract becoming an actual message queue. Don't do that now; it's premature for the stated goal ("scalable enough for a small hospital initially").

## 2. "Hospital" stays the tenant/branch unit — don't add a nested Branch model

Your spec's Part 4A asks for "Branches" under Hospital Administration. Multi-tenancy in this codebase is enforced at `Hospital` granularity — every `TenantScopedModel` FKs to `Hospital`, and the isolation guarantee (a hospital can never see another hospital's data — extensively tested, see `apps/core/tests.py`) is built on `Hospital` being the isolation boundary. **Do not nest Branch inside Hospital** — that would mean isolation now needs to be enforced at two levels, and the existing `TenantManager`/`TenantScopedViewSetMixin`/audit middleware would all need reworking to know about it.

Instead: **one `Hospital` row = one physical branch**, exactly as today. A multi-branch network is modeled as a new, thin `HospitalGroup` (name, owner org) that `Hospital` optionally FKs to, for cross-branch reporting/ownership only — it grants no cross-tenant data access by itself. This is additive (new nullable FK on `Hospital`), shipped in Phase 2, and changes nothing about how isolation works today.

**Shipped correction**: `HospitalGroup` lives in `apps.core` (next to `Hospital`), not in `apps.facilities` as originally sketched in `02-domain-model.md` — `apps.core` already can't import from a new app without creating a cycle (`facilities` needs `core.Hospital`/`TenantScopedModel`; `core` importing back from `facilities` for `HospitalGroup` would close that loop). `Ward`/`Room`/`Bed` stay in `facilities` as planned.

## 3. Patient identity stays singular — `apps.patients.Patient` gets a UHID, it doesn't get cloned

Your spec's Part 8 says CRM should hold a lightweight `Prospect`, and ERP should own `Patient`/UHID separately. Read literally, that means two person-records that must be kept in sync. Given this is one codebase, the better implementation of the same *intent* (CRM shouldn't need or see full clinical detail) is:

- Keep **one** `Patient` model (`apps.patients`) as the identity root — already true today, already used by CRM apps (enquiries, appointments, tpa) and will now also be used by ERP apps.
- Add ERP-only fields to it in Phase 2: `uhid` (unique, generated on first registration — see `02-domain-model.md`), `mrn`, `registration_type` (OPD/IPD/Emergency).
- Enforce the "CRM shouldn't see clinical detail" requirement at the **RBAC/serializer layer**, not by duplicating the row — a CRM Executive's `PatientSerializer` never includes `diagnosis`/`clinical_notes`/`prescriptions`; an ERP Doctor's does. This is what `03-rbac-and-roles.md`'s field-level visibility design is for.

One identity, role-scoped views of it. This avoids a two-way sync problem entirely, which is a bigger real-world risk than the "one Patient model" purity concern your spec raises.

## 4. External HIS coexistence

`apps/integrations` (HIS connector adapter, `HIS_CONNECTOR` setting, `HISVisit`/`HISBillingRecord`, FHIR export) is **not being replaced**. Some hospitals onboarding to this product already run a HIS and won't migrate off it. The built-in ERP being designed here becomes **one more `HIS_CONNECTOR` option** (`internal`, alongside whatever real vendors get adapters later) at the `Hospital` level — a hospital either runs its clinical operations through the modules built here, or through an existing external HIS synced via the adapter, controlled by one setting per hospital. Never both writing to the same encounter at once; that's a data-integrity problem outside this plan's scope if a hospital wants to migrate between them.

## 5. RBAC is the one true prerequisite

Everything else in this plan can be built module-by-module. RBAC cannot — the current mechanism (`apps.core.permissions.RoleBasedModelPermissions`) only gates *mutations* per Django app; `list`/`retrieve` and custom `@action`s are open to **any authenticated user at the hospital**, confirmed during the recent security review. Ship even one ERP module (say, OPD) on top of that today and every front-desk, pharmacy, and CRM-executive login can read every patient's diagnosis. `03-rbac-and-roles.md` designs the replacement (action-level permissions + record-level scoping + field-level serializer gating); `08-implementation-backlog.md` sequences it as **Phase 2, before or alongside the first clinical module, not after**.

## 6. Phase 2 status: shipped

Phase 2 (foundation — RBAC mechanism, `facilities`, `Patient` UHID, CRM role templates, frontend route gating) is implemented as of this update. See `08-implementation-backlog.md`'s Phase 2 section for the as-built detail and the handful of corrections made during implementation (noted inline where they diverge from the original sketch in this document set).
