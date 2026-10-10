# Module Hierarchy

Every module below maps to a Django app under `Backend/apps/`. **Existing** apps are marked; everything else is new. Phase numbers match `08-implementation-backlog.md` — nothing here is a "do it all at once" list.

## CRM domain (existing — apps.core.md=P1, already shipped)

| Module | App | Status |
|---|---|---|
| Lead / Enquiry Management | `enquiries` | Existing |
| Patient/Customer Relationship (CRM view) | `patients` (shared root, see `00-overview.md` §3) | Existing |
| Appointment Lead Management | `appointments` | Existing — becomes the CRM→ERP handoff point, see `05-integration-architecture.md` |
| Follow-Up Management | `automation` | Existing |
| Communication | `communications` | Existing |
| Referral Management | `referrals` | Existing |
| Corporate / B2B CRM | `packages` (`CorporateClient`, `Campaign`, `CampRegistration`) | Existing — needs contract/renewal fields, Phase 2 |
| Campaign Management | `packages.Campaign` | Existing — needs ROI/revenue-attribution fields, Phase 2 |
| Feedback & Complaints | `feedback` | Existing |
| CRM Dashboard | `analytics` | Existing (`DailyMISLog`) — needs the fuller KPI dashboard, Phase 2 |
| Telephony/Call Capture | `telephony` | Existing |
| External HIS integration | `integrations` | Existing — kept, see `00-overview.md` §4 |

## ERP domain (new)

| Module | New app | Phase | Depends on |
|---|---|---|---|
| Hospital Administration (branches, wards, rooms, beds) | `facilities` | 2 | `core.Hospital` (extend, not replace) |
| Patient Registration / UHID / MRN | `patients` (extend existing model) | 2 | — |
| RBAC overhaul (action perms, record scoping, field gating) | `core` / `accounts` (extend existing) | 2 | Nothing — this gates everything after it |
| OPD Management | `opd` | 3 — **shipped** | `facilities`, RBAC |
| Doctor / Clinical Management | `opd` + `patients` extensions | 3 — **shipped** | `opd` |
| Nursing Management | `nursing` | 4 — **shipped** | `ipd` |
| IPD / Admission Management | `ipd` | 4 — **shipped** | `facilities` (beds/wards) |
| Laboratory (LIS) | `laboratory` | 5 | `opd`/`ipd` (order source) |
| Radiology (RIS) | `radiology` | 5 | `opd`/`ipd` |
| Pharmacy | `pharmacy` | 5 | `inventory` |
| Emergency Department | `emergency` | 6 | `ipd`, `opd`, `laboratory` |
| Operation Theatre / Surgery | `ot` | 6 | `ipd`, `facilities` |
| ICU | `icu` | 6 | `ipd`, `facilities` |
| Blood Bank | `bloodbank` | 6 | `ot`/`ipd` (transfusion demand) |
| Billing | `billing` | 7 | every clinical app (line-item sources) |
| Insurance / TPA | `tpa` (existing — extend) | 7 | `billing` |
| Inventory & Procurement | `inventory` | 7 | `pharmacy` |
| Finance & Accounts | `finance` | 8 | `billing`, `inventory` |
| HR & Employee Management | `hr` | 8 | `accounts` (existing User — link, don't duplicate) |
| Discharge Management | `ipd` (extend, not a separate app — see below) | 4 — **shipped** | `ipd`, `pharmacy`, `billing` |

**Why Discharge isn't its own app:** discharge is a *state transition and a document* (discharge summary), not an independent bounded domain with its own entities — it reads from `ipd.Admission`, `laboratory`, `pharmacy`, and `billing`, and writes one `DischargeSummary` record plus an `Admission` status change. Making it a separate app would mean it imports from five other apps and owns almost nothing itself. It lives in `ipd` as `Admission.status = discharged` plus a `DischargeSummary` model, with signals notifying `billing` (final bill) and `feedback` (post-discharge NPS) — see `05-integration-architecture.md`.

**Why Nursing isn't folded into IPD:** nursing staff act across OPD, IPD, Emergency, and ICU (a triage nurse, a ward nurse, an ICU nurse are different scopes of the same profession) — `nursing` holds the shared `NursingNote`/`VitalsReading`/`MedicationAdministration` models referenced *from* `ipd`, `emergency`, and `icu` via generic relation or explicit FKs per encounter type, rather than IPD owning a concept it doesn't exclusively use.

## Full app list after all phases

```
apps/
  core/            existing, extended (Hospital gets optional HospitalGroup FK)
  accounts/        existing, extended (RBAC overhaul)
  patients/        existing, extended (UHID/MRN, clinical-visibility gating)
  telephony/       existing
  enquiries/       existing
  appointments/    existing, extended (CRM handoff signal)
  communications/  existing
  automation/      existing
  feedback/        existing
  analytics/       existing, extended (ERP ops dashboard data)
  integrations/    existing (external HIS path, kept)
  packages/        existing, extended (contracts, ROI)
  referrals/       existing
  tpa/             existing, extended (billing linkage)
  facilities/      NEW — wards, rooms, beds, hospital-group
  opd/             NEW — encounter, vitals, consultation, diagnosis
  ipd/             NEW — admission, bed allocation, transfer, discharge
  nursing/         NEW — nursing notes, vitals, medication administration
  emergency/       NEW — triage, ED visit
  laboratory/      NEW — LIS
  radiology/       NEW — RIS
  pharmacy/        NEW — dispensing, stock
  ot/              NEW — surgery scheduling, operative/anaesthesia notes
  icu/             NEW — ICU admission, monitoring
  bloodbank/       NEW — donor, unit, cross-match, transfusion
  billing/         NEW — bills, payments, discounts, refunds
  inventory/       NEW — items, purchase, GRN, suppliers
  finance/         NEW — ledger, expenses, receivables/payables
  hr/              NEW — employees, attendance, leave, payroll
```

14 new apps. See `08-implementation-backlog.md` for why they ship in that phase order (dependency-driven, not alphabetical).
