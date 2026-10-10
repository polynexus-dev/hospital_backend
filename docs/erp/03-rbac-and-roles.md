# RBAC & Roles

## 1. Why the current mechanism can't carry this

Today (`apps/accounts/permission_templates.py`, `apps/core/permissions.py`): a `Role` applies a **template** that grants Django's four stock permissions (`add`/`change`/`delete`/`view`) across **every model in a whole app**, and `RoleBasedModelPermissions` only checks those on `create`/`update`/`partial_update`/`destroy` — `list`/`retrieve` and every custom `@action` are open to any authenticated user at the hospital. That's a reasonable simplification for a CRM where the worst case of "front desk can technically list TPA claims" is low-stakes. It is not acceptable once the same mechanism is expected to keep a billing executive from reading clinical notes, or a CRM executive from reading lab results — your spec's explicit requirement.

Three gaps to close, in order of how much they change the codebase:

1. **Action-level permissions** — Approve/Cancel/Verify/Finalize/Print/Export/Assign don't exist as Django permissions today (Django auto-generates only add/change/delete/view per model). Need custom permission codenames.
2. **Record-level scoping** — "a nurse sees only her assigned patients' data," "a doctor sees only their own consultations" — today, scoping stops at the hospital tenant boundary; there's no narrower scope.
3. **Field-level gating** — "billing executive sees the bill but not the diagnosis on the same encounter" can't be expressed as a permission on a whole model; the model (`Encounter`) is legitimately visible to both roles, just not identically.

## 2. Mechanism design

### 2a. Action-level permissions

Add custom permissions via each sensitive model's `Meta.permissions`, following Django's existing `app_label.codename_model` convention so `RoleBasedModelPermissions`-style checks keep working unmodified:

```python
class LabResult(TenantScopedModel):
    ...
    class Meta:
        permissions = [
            ("verify_labresult", "Can verify a lab result"),
            ("finalize_labresult", "Can finalize/lock a lab result"),
        ]

class Bill(TenantScopedModel):
    ...
    class Meta:
        permissions = [
            ("approve_discount_bill", "Can approve a billing discount"),
            ("approve_refund_bill", "Can approve a billing refund"),
            ("finalize_bill", "Can finalize a bill"),
        ]
```

A new `ActionPermissionRequired` DRF permission class (in `apps.core.permissions`, sibling to `RoleBasedModelPermissions`) checks `request.user.has_perm(f"{app_label}.{action}_{model_name}")` for any `@action`-decorated endpoint that needs one (`verify`, `finalize`, `approve_discount`, etc.), closing the exact gap `RoleBasedModelPermissions`'s own docstring already flags ("custom actions are not gated by the model permission check" — that comment exists today specifically because this was a known, deliberate deferral).

### 2b. Record-level scoping

Extend `TenantScopedViewSetMixin.get_queryset()` (currently: filter by `hospital_id`, or by `X-Hospital-Id` for staff) with an optional **assignment scope**, declared per ViewSet, not hardcoded into the mixin:

```python
class NursingNoteViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    assignment_scope_field = "admission__bed__ward__assigned_nurses"  # or equivalent
```

If a role has `data_scope=assigned_only` (new field on `Role`, alongside the existing `template`), `get_queryset()` filters through `assignment_scope_field` in addition to tenant scoping. Roles without that flag (doctors/admins with broader legitimate access) skip it. This is additive — existing CRM ViewSets that don't set `assignment_scope_field` behave exactly as they do today.

### 2c. Field-level gating — shipped in Phase 2 for Patient/Prescription

Per-model, define two (or more) DRF serializers instead of one, and pick between them by role in `get_serializer_class()`. Shipped as `PatientSerializer` (full) vs. `PatientCRMSerializer` (drops `national_id_type`/`national_id_number`) in `apps.patients.views.PatientViewSet.get_serializer_class()`:

```python
class PatientViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    def get_serializer_class(self):
        if self.request.user.has_perm("patients.access_clinical_detail"):
            return PatientSerializer
        return PatientCRMSerializer
```

The capability permission is `patients.access_clinical_detail` on `Patient.Meta.permissions` — **not** `view_clinical_detail` as first sketched. That name was caught during implementation: `apply_permission_template`'s per-app verb sweep matches permissions by `codename.startswith(f"{verb}_")`, so a codename starting with `view_` would have been silently swept into *any* role template granted `"patients": ["view", ...]` — including `front_desk`, defeating the entire point of a selectively-granted capability flag. `access_clinical_detail` doesn't collide with any of the four standard verb prefixes.

For a model with **no** CRM-safe partial view — `apps.patients.Prescription` (diagnosis/medications/lab_orders, all clinical, no meaningful subset to show a non-clinical role) — gating the whole ViewSet is more honest than fabricating a two-field serializer nobody needs. Shipped as `apps.core.permissions.RequiresClinicalDetailPermission`, added to `PrescriptionViewSet.permission_classes` alongside the global defaults:

```python
class RequiresClinicalDetailPermission(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("patients.access_clinical_detail")
```

Granted (via the `"extra"` key in `permission_templates.py`, see §5 below) to `owner`, `admin`, and `doctor` in Phase 2; the remaining clinical role templates (`nurse`, `him_officer`, ...) pick it up when they ship in their own phase.

### 2d. `Role` model changes — shipped as designed

```python
class Role(TimeStampedModel):
    ...
    # existing: template field
    data_scope = models.CharField(choices=DataScope.choices, default=DataScope.ALL)         # "all" | "assigned_only"
    domain = models.CharField(choices=Domain.choices, default=Domain.BOTH)                    # "crm" | "erp" | "both"
```

`domain` isn't enforced by itself (a role's actual access is still its permission set) — it drives the **frontend navigation split** (`06-navigation-and-dashboards.md`) and is exposed to the frontend as `role_domain` on `GET /users/me/`.

**`permission_templates.py` shape, shipped**: each template is now `{"apps": {app_label: [verbs]}, "extra": ["app_label.codename", ...]}` rather than a bare `{app_label: [verbs]}` dict — `"extra"` is where capability flags (`patients.access_clinical_detail`) and, from Phase 3 onward, action-level permissions (`laboratory.verify_labresult`) get granted explicitly, bypassing the verb-prefix sweep entirely (see §2c above for why that sweep is exactly what a naming collision would otherwise trigger). All 5 pre-existing templates (`owner`, `admin`, `doctor`, `front_desk`, `telephony_operator`) were migrated to the new shape in the same change — `owner`/`admin`/`doctor` also gained `"extra": ["patients.access_clinical_detail"]`.

---

## 3. Full role list

### CRM roles (existing 5 templates → extended to match your spec's list)

| Role | Template key | Notable access | New vs. existing |
|---|---|---|---|
| CRM Super Admin | `crm_super_admin` | Full CRM config, users, roles, campaigns, integrations | New — today's `owner`/`admin` templates already grant this via full-app-CRUD; split out so ERP access isn't bundled in by default |
| CRM Manager | `crm_manager` | Leads, enquiries, follow-ups, campaigns, referrals, corporate, CRM reports | New |
| CRM Executive | `crm_executive` | Assigned leads/enquiries/follow-ups/communication history | Extends existing `front_desk`-adjacent scope; explicitly excludes `opd`/`ipd`/clinical apps |
| Call Centre Executive | `call_centre_executive` | Enquiries, leads, appointment requests, communication | ≈ existing `telephony_operator`, renamed/scoped |
| Marketing Manager | `marketing_manager` | Campaigns, sources, conversion, ROI reports | New |
| Corporate Relationship Manager | `corporate_rm` | `packages.CorporateClient`, contracts, referrals | New |
| CRM Auditor | `crm_auditor` | Read-only across CRM apps | New — `data_scope=all`, every CRM permission at `view` only |

### ERP roles (all new)

| Role | Template key | Notable access | Data scope |
|---|---|---|---|
| Hospital ERP Super Admin | `erp_super_admin` | Full ERP config | all |
| Hospital Administrator | `hospital_administrator` (**shipped, Phase 3**) | `facilities` full CRUD, `opd`/`appointments`/`patients`/`accounts`/`analytics` view-only; no `access_clinical_detail` by default (org-policy decision, see permission_templates.py comment) | all |
| Receptionist / Front Desk | `receptionist` (**shipped, Phase 3**) | `patients`/`appointments` add/change, `facilities` view; deliberately **no** `opd` app access at all (not just no clinical-detail — no access to the app), matching "shouldn't access clinical info unnecessarily" | all (front-desk data), no `access_clinical_detail` |
| Doctor | `doctor` (**extended, Phase 3 + 4**: `opd` add/view/change + `opd.finalize_clinicalnote`/`opd.finalize_diagnosis` in Phase 3; `ipd` add/view/change + `ipd.finalize_doctorprogressnote`/`ipd.finalize_dischargesummary` in Phase 4, all on the same Phase-2 base grant) | Assigned-patient history, OPD/IPD, diagnosis, prescriptions, orders, progress notes, discharge summaries; finalize their own clinical notes/diagnoses/progress notes/discharge summaries | assigned_only (opt-in per Role — see `03` §2b; the template grants the *permission*, a hospital admin sets `data_scope=assigned_only` on the actual Role instance) |
| Nurse | `nurse` (**shipped, Phase 4**) | `nursing` app (view/add/change — notes, medication administration, intake/output); `opd`/`ipd`/`patients`/`facilities` view-only, `access_clinical_detail` granted | all (see caveat below — not actually `assigned_only`) |
| Lab Technician | `lab_technician` (**shipped, Phase 5**) | Lab orders, sample collection, result entry (not verify) | all (see caveat below — not actually `assigned_only`) |
| Lab Pathologist / Lab Manager | `lab_manager` (**shipped, Phase 5**) | Verify/finalize results (`laboratory.verify_labresult`), lab config | all (lab app) |
| Radiology Technician | `radiology_technician` (**shipped, Phase 5**) | Radiology orders, report entry (not verify) | all (see caveat below) |
| Radiologist | `radiologist` (**shipped, Phase 5**) | Create/verify/finalize radiology reports (`radiology.verify_radiologyreport`) | all (radiology app) |
| Pharmacist | `pharmacist` (**shipped, Phase 5**) | View prescriptions, dispense, pharmacy stock (`pharmacy.add_dispenserecord`/`add_stockadjustment`) | all (pharmacy app) |
| OT Manager | `ot_manager` | OT schedule, resources, staff, consumables | all (ot app) |
| Surgeon | `surgeon` | Surgery requests, operative notes, `finalize_operativenote` | assigned_only |
| Anaesthetist | `anaesthetist` | Pre-anaesthesia, anaesthesia records, `finalize_anaesthesiarecord` | assigned_only |
| ICU Staff | `icu_staff` | Assigned ICU patients: vitals, monitoring, medication, notes | assigned_only |
| Blood Bank Technician | `blood_bank_technician` | Donors, blood units, cross-matching, transfusions (`bloodbank` full CRUD), `access_clinical_detail` granted | all (bloodbank app) |
| Billing Executive | `billing_executive` | Create bills, view charges/payments; no `view_clinical_detail` | all (billing app) |
| Billing Manager | `billing_manager` | `approve_discount_bill`, `approve_refund_bill`, reconciliation | all (billing app) |
| Insurance / TPA Executive | `insurance_tpa_executive` | Insurance details, billing, pre-auth, claims — limited clinical doc access (attachment-level, not full chart) | all (tpa/billing), scoped clinical attachment access only |
| Inventory Manager | `inventory_manager` | Inventory, stock, purchase, suppliers, GRN | all (inventory app) |
| Purchase Manager | `purchase_manager` | Requisitions, POs, vendors, approval workflow | all (inventory app) |
| Finance Manager | `finance_manager` | Billing, collections, expenses, receivables/payables, reports | all (finance/billing) |
| HR Manager | `hr_manager` | Employee records, attendance, leave, payroll | all (hr app), no `view_clinical_detail` |
| Medical Records / HIM Officer | `him_officer` | Patient records, discharge summaries, record-correction workflow | all, `view_clinical_detail` granted |
| Hospital Auditor | `hospital_auditor` | Read-only across authorized ERP modules + full audit log access | all, view-only |

That's 7 CRM + 23 ERP = 30 role templates, matching your spec's count. Each becomes one entry in `PERMISSION_TEMPLATES` (`apps/accounts/permission_templates.py`), same mechanism as today's 5 — this is a data/config change (new dict entries), not a new subsystem, once §2's action/scope/field mechanisms exist.

**Two honest gaps surfaced shipping `nurse` in Phase 4**, both consequences of decisions already documented above, not new problems:

1. **Nurse isn't actually `assigned_only`.** §2b's `assignment_scope_field` mechanism needs a real "assigned to this user" relation to filter on — `doctor.user` exists (so `doctor`'s `assigned_only` works today), but no equivalent "nurse assigned to this ward/shift" model exists yet. Building one is a real feature (ward/shift roster management), not a config tweak, so it's deferred rather than faked. `nurse` ships with `data_scope=all` within its granted apps; a hospital that needs per-nurse ward restriction has to wait for that model.
2. **`nurse` doesn't get `opd.add_vitalsreading`** even though vitals recording is realistically a nursing task, because §1's per-app (not per-model) permission sweep means granting `"opd": ["add"]` would also grant `add_clinicalnote`/`add_diagnosis` — doctor-authored content nurses shouldn't be able to create. `nurse` ships `opd`/`ipd` as view-only instead. A hospital that wants nurses recording OPD vitals can grant `opd.add_vitalsreading` directly via Django admin's per-permission editor (same escape hatch this module's docstring already describes for any template).

**The same `assigned_only` gap resurfaced shipping `lab_technician`/`radiology_technician` in Phase 5**, for the identical reason as `nurse` above: §2b's `assignment_scope_field` mechanism needs a real "assigned to this user" relation to filter `LabOrder`/`RadiologyOrder` on, and no "this technician's shift/bench" model exists. Both ship `data_scope=all` within their granted apps rather than a faked scope — a technician sees every order in the hospital, not just ones assigned to them. Unlike `doctor`'s `assigned_only` (which has `Doctor.user` to filter on), there's no natural per-technician ownership field on `LabOrder`/`SampleCollection` to add without inventing a scheduling concept out of scope for this phase.

## 4. Permission matrix (representative slice — full matrix is a spreadsheet artifact generated from `PERMISSION_TEMPLATES`, not hand-maintained prose)

| Role | Patients (clinical) | OPD/Encounter | Lab Results | Billing | Discount Approval | HR | CRM Leads |
|---|---|---|---|---|---|---|---|
| Doctor | view/change (assigned) | view/add/change (assigned) | view (assigned) | — | — | — | — |
| Nurse | view (assigned) | view (assigned) | view (assigned) | — | — | — | — |
| Billing Executive | view (non-clinical fields only) | — | — | add/change | — | — | — |
| Billing Manager | view (non-clinical fields only) | — | — | add/change/view | **approve** | — | — |
| CRM Executive | view (CRM fields only) | — | — | — | — | — | view/add/change (assigned) |
| Lab Technician | view (assigned) | — | add (not verify) | — | — | — | — |
| Lab Manager | view (assigned) | — | view/add/**verify**/**finalize** | — | — | — | — |
| HR Manager | — | — | — | — | — | full | — |
| Hospital Auditor | view (read-only, all) | view (read-only) | view (read-only) | view (read-only) | — | view (read-only) | view (read-only) |

The generated version of this (one row per role × one column per model × permission-verb cells) belongs in `PERMISSION_TEMPLATES` directly and in an auto-exported CSV/spreadsheet for stakeholder review — not maintained by hand in two places. Treat this table as the *shape* of the matrix, not the source of truth once Phase 2 ships.

## 5. What ships when

RBAC mechanism (§2) is **Phase 2**, alongside `facilities` and the `Patient` UHID extension — before any clinical module. Role templates (§3) ship incrementally, **each new role template lands in the same phase as the module it governs** (e.g. `lab_technician`/`lab_manager` ship with Phase 5's `laboratory` app), not all 30 at once in Phase 2 — most of them are meaningless until their module exists. See `08-implementation-backlog.md`.

**Phase 2 status: shipped.** The mechanism (§2a-d) is live; the 7 CRM role templates (§3's CRM table) are live.
- `ActionPermissionRequired` — first real consumer shipped in Phase 3 (`opd.finalize_clinicalnote`/`opd.finalize_diagnosis`); by Phase 5 also gates `laboratory.verify_labresult`/`radiology.verify_radiologyreport`. Verified in Phase 2 with isolated unit tests against the permission class's own logic before any real caller existed.
- `assignment_scope_field` — first real consumer shipped in Phase 4 (`ipd`'s `assigned_only` doctor scoping). Still has no consumer in `laboratory`/`radiology`/`pharmacy` as of Phase 5 — see §3's Phase 5 gap note above (no per-technician "assigned" relation exists yet). Verified in Phase 2 against `apps.core.viewsets.TenantScopedViewSetMixin` directly, using `patients.Document.uploaded_by` as a stand-in "assigned to" relation.

Both are real, tested code — just infrastructure ahead of its first caller, same as `apps.core.audit.log_action()` sat unwired for a while before this pass gave it its first caller (`AuditedModelViewSetMixin`, see `07-audit-and-security.md`).
