# Audit & Security Model

Cross-reference: `docs/SECURITY_COMPLIANCE.md` is the standing compliance/gap document for this codebase (ISO 27001, DPDP Act, NABH, SPDI Rules). Everything below is additive to that document, specific to what the ERP build introduces — read that document first; don't duplicate its findings here.

## 1. What already exists and is reused as-is

- `apps.core.models.AuditLog` — append-only (DB-trigger enforced, not just app-level), records actor/hospital/action/model/object/method/path/status/IP. Every ERP mutation gets this automatically via the existing global `AuditMiddleware` — no new work required for the coarse "who hit which endpoint when" layer.
- `apps.core.encryption.EncryptedTextField` / `compute_blind_index()` — already shipped for `Patient.national_id_number`/`insurance_policy_number` and `PreAuthRequest.policy_number`. House style (per `02-domain-model.md`) for every new sensitive identifier field (`hr.Employee.bank_account_number`/`pan_number`, any new govt-ID or financial-identifier field).

## 2. What's missing and must ship with the ERP, not after it

### 2a. Field-level diffs (`apps.core.audit.log_action()`) — shipped, as a ViewSet mixin, not a model mixin

This function existed but was never called anywhere in the codebase — confirmed dead code during the security review, now fixed. **Shipped correction from the original sketch below**: this is `apps.core.viewsets.AuditedModelViewSetMixin`, not a `Model.save()` hook. A model-level hook can't reliably get the acting user — this codebase already learned that lesson once, the hard way, for tenant scoping: `TenantScopedViewSetMixin`'s own docstring documents that `apps.core.tenancy`'s contextvar is populated by `TenantMiddleware` *before* DRF resolves JWT authentication, so it's unset for real API traffic at that point. `log_action()` itself had the identical bug (`hospital_id=get_current_hospital_id()`) — fixed during this pass to derive `hospital_id` from the audited instance instead, the same category of fix as `TenantScopedViewSetMixin`'s.

Shipped as:

```python
# apps/core/viewsets.py
class AuditedModelViewSetMixin:
    audited_fields: tuple = ()   # which fields to diff — not every field, or a diagnosis edit drowns in updated_at noise

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._log("create", serializer.instance)

    def perform_update(self, serializer):
        old = type(serializer.instance).objects.get(pk=serializer.instance.pk)
        super().perform_update(serializer)
        self._log("update", serializer.instance, old=old)

    def perform_destroy(self, instance):
        self._log("delete", instance)
        super().perform_destroy(instance)
```

`self.request.user` inside a ViewSet method is DRF's own already-resolved user — the same known-correct source `TenantScopedViewSetMixin.get_queryset()` itself relies on. No real ViewSet mixes this in yet (the first will be `opd.ClinicalNote`/`Diagnosis` in Phase 3); it's built, and its diff/no-op-suppression logic is exercised by targeted tests in `apps/core/tests.py` ahead of that first real caller — same "infrastructure ready, first Phase 3+ consumer later" status as `ActionPermissionRequired`/`assignment_scope_field` in `03-rbac-and-roles.md` §5.

Once Phase 3+ models mix this in, wire it for at least: `Diagnosis`, `ClinicalNote`, `LabResult` (value/flag), `DischargeSummary`, `Bill`/`Discount`/`Refund` amounts, `Prescription`.

### 2b. Finalize / amendment workflow (your spec's "finalized clinical records should not be silently edited")

Every clinical/financial model in `02-domain-model.md` marked with a `finalized_at` field follows the same state machine:

```
draft (editable) ---finalize()---> finalized (locked)
                                        |
                                        v
                              correction requested
                                        |
                                        v
                          Amendment record created (new row,
                          references original, both retained)
```

Concretely: `finalized_at IS NOT NULL` blocks `save()` on protected fields (enforced in the model's `save()`, not just the serializer — a Django admin edit or a script must be blocked too, matching the existing `AuditLog` DB-trigger philosophy of "enforce it below the ORM, not just in front of it" wherever the stakes justify it). Changing a finalized diagnosis, lab result, or discharge summary requires a new `Amendment` model instance (`original` FK, `corrected_value`, `reason` TEXT required, `amended_by` FK, `amended_at`) — the original row is never overwritten. This is the direct implementation of your spec's Part 9 requirement and applies to: `ClinicalNote`, `Diagnosis`, `LabResult`, `RadiologyReport`, `OperativeNote`, `AnaesthesiaRecord`, `DischargeSummary`, `Bill` (post-payment).

`finalize_*`/`verify_*` custom permissions (`03-rbac-and-roles.md` §2a) gate who can transition `draft → finalized` in the first place.

**Shipped in Phase 2**: `apps.core.models.FinalizableModel` (abstract mixin, `finalized_at`/`finalized_by` + the save()-guard + `.finalize(user)`) and `apps.core.models.Amendment` (concrete, generic-FK — one table for corrections to any finalizable model, not one Amendment table per model). `Amendment` is concrete and directly tested (create one against an existing model instance via its generic relation, confirm `content_object` resolves). `FinalizableModel` is abstract with zero concrete subclasses until Phase 3's first clinical model (`opd.ClinicalNote`/`Diagnosis`) — its save()-guard logic is correct by construction and code review, but genuinely gets its first behavioral test coverage from that first real subclass, not fabricated coverage against a throwaway model built just to exercise it.

### 2c. Consent (relevant once ERP handles real procedures)

`OT.PreOpChecklist.consent_obtained` (a boolean today, per `02-domain-model.md`) is a placeholder, not a real consent-management system — flag it explicitly here rather than let it look more complete than it is. `docs/SECURITY_COMPLIANCE.md` finding M2 already calls out that this codebase has no structured consent model anywhere (the closest thing is a free-text "Consent form" document category on `Patient`). Surgical/procedural consent specifically (who consented, for what, informed-of-risks confirmation, signature/witness) is out of this ERP plan's scope to fully design — treat `PreOpChecklist.consent_obtained` as "was a consent process completed," pointing at a scanned/uploaded document via the existing `patients.Document` model, until a dedicated consent-management design is scoped separately. Don't let OT ship implying more consent rigor than actually exists.

### 2d. Data-scope enforcement is a security control, not just a UX filter

`03-rbac-and-roles.md` §2b's `assignment_scope_field` is the mechanism that makes "a nurse shouldn't see unassigned patients' charts" actually true, not just visually tidy. It must be enforced in `get_queryset()` (server-side, unconditional) — never only in the frontend nav (`06-navigation-and-dashboards.md` §5 flags this exact class of bug already existing for `/admin`; don't reintroduce the same mistake for clinical data, where the consequence is a PHI exposure, not just a UI leak).

## 3. Sequencing

All of §2 is Phase 2 infrastructure (the mixin, the finalize/`Amendment` base pattern, the scope-enforcement mechanism) even though most of the *models* that use it ship in later phases — same reasoning as the RBAC mechanism in `03-rbac-and-roles.md` §5. Building `AuditedModel`/finalize-locking once, correctly, before the first clinical model exists is far cheaper than retrofitting it across 14 apps after the fact.
