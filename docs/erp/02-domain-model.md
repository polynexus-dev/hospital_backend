# Domain Model & API Conventions

## Conventions every new app follows (already established by the CRM apps — don't invent new ones)

- Every model extends `apps.core.models.TenantScopedModel` (adds `hospital` FK + `TenantManager`) unless it's explicitly cross-tenant reference data.
- Every ViewSet extends `apps.core.viewsets.TenantScopedViewSetMixin` — tenant-scoped `get_queryset()`, `perform_create()` stamps `hospital` automatically. No new app reinvents tenant filtering.
- `AuditMiddleware` covers every mutating request automatically (already global). Sensitive-field diffs use `apps.core.audit.log_action()` — currently unwired dead code; Phase 2 wires it in for every ERP write path, see `07-audit-and-security.md`.
- REST endpoints: `/api/v1/<app>/<resource>/`, DRF `ModelViewSet` + router, `drf-spectacular` schema — identical to every existing app. No separate "API spec" beyond what each model section below implies; the schema is generated, not hand-written.
- IDs: UUID for new top-level aggregate roots that might be referenced externally (e.g., `Admission`, `LabOrder`) matching `Hospital`'s convention; BigAutoField for high-volume child records (e.g., `VitalsReading`), matching `Patient`'s convention.
- Money: `DecimalField(max_digits=12, decimal_places=2)`, matching `tpa`/`packages`.
- Sensitive identifiers (any new field holding a govt ID, policy number, or equivalent): `apps.core.encryption.EncryptedTextField`, matching the pattern already shipped for `Patient.national_id_number` / `PreAuthRequest.policy_number`. Apply this rule to `Employee` bank account/PAN fields in `hr`, and to any new identifier field before it ships — it's now house style, not optional.

---

## facilities (Phase 2 — shipped)

`HospitalGroup` actually lives in `apps.core` (see `00-overview.md` §2's shipped-correction note), not here — `apps.core` can't import a model from an app that itself depends on `apps.core`. Shipped as:

```
# apps.core
HospitalGroup         name, owner_org_name
  Hospital.group       FK → HospitalGroup, null=True   (extends existing Hospital)
  Hospital.next_uhid_sequence   PositiveIntegerField, default=1, editable=False — backs Patient's UHID generator below

# apps.facilities
Ward                  hospital FK, name, ward_type[general|semi_private|private|icu|ot_prep], department FK(null), floor, is_active
Room                   ward FK, room_number, room_type
Bed                    room FK, bed_number, bed_type[general|semi_private|private|icu|ventilator],
                        status[available|occupied|maintenance|reserved]
```

`Bed.current_admission` (FK → `ipd.Admission`) is **deferred to Phase 4** — you can't FK to a model that doesn't exist yet. `Bed.status` ships now as a plain field set directly by whoever's managing the bed; it becomes signal-driven (admission/transfer/discharge flip it automatically, never edited by two code paths) once `ipd.Admission` exists in Phase 4, per the original design intent below.

`Bed.status` is meant to become the single source of truth for occupancy once Phase 4 ships — every admission/transfer/discharge will flip it via signal (a `BedAllocation` audit trail, see `ipd` below, records history; `Bed.status`/`current_admission` becomes just the current-state cache for fast dashboard queries).

## patients (extend existing app, Phase 2 — shipped)

```
Patient (extend existing model):
  + uhid                 CharField, unique=True, null=True (see migration note below), generated on first save (format: <hospital.slug>-<sequence>)
  + mrn                  CharField, blank — for hospitals that also track a separate legacy MRN
  + registration_type    choices[opd|ipd|emergency] — how they first entered the ERP
  + blood_group          CharField, choices, blank
```

No new Patient-like model. CRM continues using the same row; role-scoped serializers (see `03-rbac-and-roles.md`) decide what a given caller sees — shipped as `PatientSerializer` (full) vs. `PatientCRMSerializer` (drops `national_id_type`/`national_id_number`), selected in `PatientViewSet.get_serializer_class()` by whether the caller has `patients.access_clinical_detail`.

**Migration pattern established here, reusable for any future "add a unique field to a table with existing rows" case**: `uhid` needed `null=True` (not just `blank=True`) — SQL unique constraints treat every `NULL` as distinct, so adding a nullable unique column to a populated table is a safe, non-blocking `AddField`. A single follow-up data migration (`0010_backfill_uhid`) then assigns a real UHID to every pre-existing row via the same per-hospital sequence counter (`Hospital.next_uhid_sequence`) new rows use — verified end-to-end against simulated legacy data (backfill assigns unique sequential UHIDs; a new patient created afterward correctly continues the sequence with no collision). Contrast with the `EncryptedTextField` migrations (widen → data-migrate → swap field class) — that 3-step shape was needed there because the *type* of an existing column had to change; here only *values* needed backfilling, so `null=True` was enough to skip the "widen" step entirely.

## opd (Phase 3 — shipped, with one significant design correction)

**Correction found during implementation**: `apps.appointments.Appointment` already owns a full visit state machine (`booked → confirmed → checked_in → in_consult → diagnostics → completed`, plus `queue_token`, `checked_in_at`, `completed_at` — all pre-existing, tested, and already driving a working OPD queue UI). The `Encounter.status`/`token_number`/`checked_in_at`/`consultation_started_at`/`consultation_completed_at` fields sketched below were never built — they'd have been a second, competing state machine for the same visit. Shipped `Encounter` is a pure clinical-content container, one-to-one with `Appointment`, holding only what `Appointment` has no room for:

```
Encounter               appointment FK(OneToOne, → appointments.Appointment), patient FK, doctor FK(appointments.Doctor),
                          department FK(nullable)
                          -- created automatically at check-in, see apps.opd.signals — not created/updated by the API in practice

VitalsReading            encounter FK, recorded_by FK(User), height_cm, weight_kg, bp_systolic, bp_diastolic,
                          pulse, temperature_c, spo2, recorded_at

ClinicalNote              encounter FK(OneToOne), doctor FK, chief_complaints TEXT, history TEXT, examination_findings TEXT,
                          finalized_at/finalized_by (FinalizableModel — see 07-audit-and-security.md)

Diagnosis                encounter FK, icd_code, description, diagnosis_type[provisional|final], created_by FK,
                          finalized_at/finalized_by (FinalizableModel)

InvestigationOrder        encounter FK, order_type[lab|radiology], description(freeform until Phase 5), status[ordered|in_progress|completed], created_by FK

Prescription (existing model in apps.patients) — gained a nullable `encounter` FK (string reference "opd.Encounter" to avoid a circular import), not moved.
```

`apps.appointments.Doctor` gained a nullable `user` FK to `accounts.User` — needed so `assignment_scope_field="doctor__user"` (§2b of `03-rbac-and-roles.md`) has something to match the requesting user against; a Doctor directory entry without a login (a visiting consultant whose slots front desk manages) simply has `user=None` and is invisible to assigned-only scoping, which is the correct behavior.

Don't delete or rewrite `Prescription`; it's live, tested code that predates this app.

## ipd (Phase 4 — shipped)

```
Admission                patient FK, admitting_doctor FK(appointments.Doctor), department FK, bed FK(→ facilities.Bed),
                          source_encounter FK(→ opd.Encounter, nullable — see 05-integration-architecture.md for why this
                          is a plain back-reference and not an event-triggered creation),
                          admission_type[planned|emergency], status[admitted|discharged|dama|deceased],
                          admitted_at, discharged_at(nullable), admission_diagnosis TEXT

BedAllocation             admission FK, bed FK, allocated_at, released_at(nullable)
                          -- history trail; Admission.bed is current, this is "every bed this admission has occupied"

WardTransfer              admission FK, from_bed FK, to_bed FK, reason TEXT, requested_by FK, approved_by FK(nullable), transferred_at
                          -- backend complete (request + approve, both re-locking beds via select_for_update same as
                          -- admit_patient); no frontend yet, see docs/erp/08-implementation-backlog.md Phase 4 status

DoctorProgressNote        admission FK, doctor FK, note TEXT, created_at, finalized_at/finalized_by (FinalizableModel)

DischargeSummary          admission FK(one-to-one), final_diagnosis TEXT, procedures_performed TEXT,
                          treatment_summary TEXT, discharge_medications TEXT, follow_up_instructions TEXT,
                          discharge_type[routine|dama|referred|deceased], prepared_by FK, finalized_at/finalized_by (FinalizableModel)
```

`Admission.bed`/`WardTransfer.from_bed`/`to_bed` use `on_delete=PROTECT` — a bed with admission history can't be deleted out from under it. `apps.ipd.services` owns every state transition (`admit_patient`, `request_ward_transfer`/`approve_ward_transfer`, `discharge_patient`) — each re-locks the relevant `Bed` row with `select_for_update()`, same race-safety pattern as `apps.appointments.services.book_appointment`. `discharge_patient()` requires a `DischargeSummary` to already exist (raises `DischargeSummaryRequired` otherwise, surfaced as a 400) — not necessarily finalized, since a hospital may finalize the paperwork shortly after the patient physically leaves.

## nursing (Phase 4 — shipped, shared by ipd/emergency/icu)

```
NursingNote               content_type + object_id (generic FK — Admission today, EDVisit/ICUAdmission in Phase 6), nurse FK, note TEXT, created_at
MedicationAdministration   content_type + object_id, prescription FK(→ patients.Prescription, nullable), medication_name, dose, nurse FK, administered_at, notes
IntakeOutput                content_type + object_id, recorded_by FK, intake_ml, output_ml, recorded_at, notes
```

**Shipped correction**: `MedicationAdministration` doesn't have a "prescription_item reference" as originally sketched — `patients.Prescription.medications` is a `JSONField` list (`[{"name": ..., "dosage": ..., "duration": ...}]`), not individually-addressable rows, so there's no line-item model to FK into. It FKs the whole `Prescription` instead (nullable, for traceability) plus its own `medication_name`/`dose` text fields for what was actually administered — matching the same "freeform until there's a real normalized model" precedent as `opd.InvestigationOrder`.

Generic relation (not one FK per encounter type) because a nursing note's shape is identical regardless of ward/ICU/ED — duplicating the model three times just to get three FK names would violate the same "don't merge distinct domains" principle the spec asks for elsewhere, just inverted (this is one domain, artificially split by encounter type would be the mistake). The API doesn't expose raw `content_type`/`object_id` to clients — each serializer accepts a friendlier `admission` field (an Admission PK) and resolves it server-side (`apps.nursing.serializers._AdmissionScopedSerializer`), and each ViewSet supports `?admission=<id>` filtering (`apps.nursing.views.AdmissionFilteredMixin`) despite the generic relation not being reachable by a plain `filterset_fields` entry.

## laboratory (Phase 5) — SHIPPED

```
LabTest                  name, code, department[hematology|biochemistry|microbiology|...], reference_range, unit, price
LabTestPackage             name, tests M2M LabTest, price
LabOrder                  investigation_order FK(→ opd.InvestigationOrder, nullable — orders can also come from ipd/emergency),
                          patient FK, ordered_tests M2M LabTest, status[ordered|sample_collected|processing|resulted|verified], ordered_by FK
SampleCollection            lab_order FK, sample_type, barcode(unique), collected_by FK, collected_at
LabResult                 lab_order FK, lab_test FK, value, unit, reference_range, flag[normal|high|low|critical],
                          entered_by FK, finalized_by FK(nullable), finalized_at(nullable)
```

**Shipped correction**: `LabResult.verified_by`/`verified_at` as originally sketched don't exist as separate fields — `LabResult` extends `FinalizableModel` (the same reusable lock-on-finalize mixin as `opd.ClinicalNote`/`ipd.DoctorProgressNote`), so it's `finalized_by`/`finalized_at` instead, with `laboratory.verify_labresult` as the exposed permission/action name so the API still speaks the lab industry's own term. A `critical` flag on save (via `LabResultViewSet`, not the model's `save()` itself) triggers the alert event in `05-integration-architecture.md`.

## radiology (Phase 5) — SHIPPED

```
RadiologyProcedure          name, modality[xray|ct|mri|usg|other], price
RadiologyOrder              investigation_order FK(nullable), patient FK, procedure FK, status[ordered|scheduled|completed|reported], ordered_by FK
RadiologyReport             radiology_order FK(one-to-one), findings TEXT, impression TEXT,
                          reported_by FK(radiologist), finalized_by FK(nullable), finalized_at(nullable), image_file (single FileField, reuse Document validators from apps.patients)
```

**Shipped correction**: same `verified_by/at` → `finalized_by/at` correction as `LabResult` above. `image_files` (plural, multi-attachment) shipped as a single `image_file` — a full multi-image study would need its own child model, out of scope for what this system needs beyond attaching the report's key image.

## pharmacy (Phase 5) — SHIPPED

```
Medicine                  name, generic_name, form[tablet|syrup|injection|...], unit, reorder_level
MedicineBatch               medicine FK, batch_number, expiry_date, quantity_available, mrp, purchase_price, supplier FK(→ pharmacy.Supplier, interim)
DispenseRecord              prescription FK(→ apps.patients.Prescription, nullable), batch FK, quantity, dispensed_by FK, dispensed_at
StockAdjustment             batch FK, adjustment_type[damage|expiry|correction], quantity_delta, reason, adjusted_by FK
```

**Shipped correction**: `supplier FK(→ inventory.Supplier)` as originally sketched doesn't resolve yet — `inventory` is still Phase 7. Shipped with a deliberately minimal `pharmacy.Supplier` instead (its own docstring documents it as to-be-migrated onto `inventory.Supplier` and retired, not extended in place, once Phase 7 ships). `DispenseRecord` FKs `Prescription` directly (nullable, for OTC/walk-in dispensing with no prescription behind it) rather than an `encounter` FK — matches how `MedicationAdministration` already resolved the same "no line-item model to point at" problem in Phase 4.

## emergency (Phase 6)

```
EDVisit                   patient FK, status[triaged|in_treatment|admitted|discharged|referred_out|deceased], arrived_at
Triage                    ed_visit FK(one-to-one), triage_category[1_resuscitation|2_emergent|3_urgent|4_less_urgent|5_non_urgent],
                          triaged_by FK, triaged_at
```

`EDVisit` that results in admission creates an `ipd.Admission` with `admission_type=emergency` and links back via `Admission.source_ed_visit` FK — not a copy of the visit, a reference.

## ot (Phase 6)

```
SurgeryRequest             patient FK, admission FK(nullable — day-care surgery may skip IPD), requested_by FK,
                          proposed_procedure, status[requested|approved|scheduled|completed|cancelled]
OTSchedule                 surgery_request FK, operation_theatre_room, surgeon FK, anaesthetist FK, scheduled_start, scheduled_end
PreOpChecklist              surgery_request FK, consent_obtained BOOLEAN, fasting_confirmed BOOLEAN, ... , completed_by FK
OperativeNote               ot_schedule FK(one-to-one), procedure_performed TEXT, findings TEXT, surgeon FK, started_at, ended_at, finalized_at(nullable)
AnaesthesiaRecord           ot_schedule FK(one-to-one), anaesthesia_type, intra_op_notes TEXT, anaesthetist FK, finalized_at(nullable)
ConsumableUsage              ot_schedule FK, item FK(→ inventory.Item), quantity
ImplantUsage                 ot_schedule FK, implant_name, serial_number, quantity
```

## icu (Phase 6)

```
ICUAdmission                admission FK(→ ipd.Admission, one-to-one), bed FK(→ facilities.Bed, icu type),
                          ventilator_required BOOLEAN, admitted_at, discharged_at(nullable)
VentilatorLog                icu_admission FK, mode, settings JSONField, recorded_by FK, recorded_at
ICUDailyProgressNote          icu_admission FK, doctor FK, note TEXT, created_at, finalized_at(nullable)
```

## bloodbank (Phase 6)

```
Donor                      name, blood_group, phone, last_donation_date
BloodUnit                   donor FK(nullable — can be from external blood bank), blood_group, component[whole_blood|prbc|ffp|platelets],
                          collection_date, expiry_date, status[available|reserved|issued|discarded]
CrossMatchRequest             patient FK, blood_group_required, component, requested_by FK, status[pending|matched|failed]
Transfusion                  blood_unit FK, patient FK, admission FK(nullable), issued_by FK, transfused_at, reaction_notes TEXT(blank)
```

## billing (Phase 7)

```
Bill                       patient FK, admission FK(nullable), encounter FK(nullable), bill_type[opd|ipd|pharmacy|combined],
                          status[draft|finalized|paid|partially_paid|cancelled], created_by FK
BillLineItem                 bill FK, source_type[consultation|lab|radiology|pharmacy|room|procedure|surgery], source_object_id,
                          description, quantity, unit_price, amount, discount_amount
Payment                    bill FK, amount, payment_mode[cash|card|upi|insurance|bank_transfer], received_by FK, received_at
Discount                   bill_line_item FK, amount, reason, approved_by FK(nullable — see RBAC's approve_discount permission)
Refund                     bill FK, amount, reason, approved_by FK, refunded_at
```

`BillLineItem.source_type` + `source_object_id` (not a FK per source) because a bill aggregates line items from 7+ different apps (lab, radiology, pharmacy, OT, room charges, consultation, procedures) — a real FK per type would mean `BillLineItem` importing from every clinical app, which inverts the dependency direction this whole plan is built to avoid (clinical apps shouldn't need to know about billing to exist, but billing legitimately needs to reference them — a loose generic reference, not a FK, keeps that one-directional).

## inventory (Phase 7)

```
Supplier                  name, contact, gstin
Item                       name, category[pharmacy|surgical|general], unit, reorder_level
PurchaseRequisition          item FK, quantity_requested, requested_by FK, status[pending|approved|rejected]
PurchaseOrder                supplier FK, requisition FK(nullable), status[draft|sent|partially_received|received], created_by FK
GRN                        purchase_order FK, received_items JSONField or line-item child model, received_by FK, received_at
StockTransfer                item FK, from_location, to_location, quantity, transferred_by FK
```

## finance (Phase 8)

```
Ledger                     hospital FK, entry_type[revenue|expense], category, amount, reference_type, reference_id, entry_date
Expense                     category, amount, paid_to, paid_by FK, expense_date, approved_by FK(nullable)
Receivable                  source_type[insurance_claim|corporate_billing], source_id, amount, due_date, status[pending|received|written_off]
```

Kept intentionally thin — full general-ledger accounting is out of scope for what a hospital-ops platform needs; this exists to answer "department revenue," "doctor revenue," "insurance receivables aging" reporting questions, not to replace Tally/QuickBooks. Flag this scope choice explicitly to the hospital ops stakeholder before Phase 8 — if they need full statutory accounting, that's an accounting-software integration, not a new `finance` app to build.

## hr (Phase 8)

```
Employee                   user FK(→ accounts.User, one-to-one, nullable — not every employee logs in), employee_code,
                          department FK, designation, date_of_joining, employment_type[permanent|contract|visiting],
                          bank_account_number EncryptedTextField, pan_number EncryptedTextField
Attendance                 employee FK, date, check_in, check_out, status[present|absent|half_day|leave]
LeaveRequest                employee FK, leave_type, start_date, end_date, status[pending|approved|rejected], approved_by FK(nullable)
Shift                      employee FK, shift_date, shift_type[morning|evening|night]
```

`Employee.user` is nullable and separate from `accounts.User` deliberately — not every HR employee (housekeeping, security) needs a login, but every logged-in clinical/CRM staff member should have exactly one `Employee` row once HR ships, linked, not duplicated (matches your spec's "Clinical users and hospital staff should be linked to their ERP roles").
