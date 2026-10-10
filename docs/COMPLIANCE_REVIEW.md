# Compliance review — audit, encryption, DPDP Act 2023, NABH

Engineering review of what the code does today, to support (not replace) a
formal assessment. **Nothing here is a certification or a legal opinion.**
Reviewed 10 Oct 2026, branch `feature/dual-mode-licensing`.

## Audit trail

| Requirement | Status |
|---|---|
| Every create / update / delete is logged: who, when, from where | **In place.** `AuditMiddleware` writes an `AuditLog` row for every mutating request (user, method, path, IP, status). The table is immutable at the database level (core migration 0003). |
| What changed on patient records | **In place for the patient record** (added in this review): field-level diffs on create/update/delete of `Patient`. Encrypted fields are logged as *changed* without values. Also already in place for bills, payments, claims, blood bank. |
| What changed on other clinical records | **Gap.** Documents, prescriptions, clinical notes, allergies, orders and similar records only have the request-level log (which record, who, when, where), not field diffs. Add `AuditedModelViewSetMixin` to those viewsets. |
| Read access to patient records | Request-level reads of patient-record URLs are logged; per-record read capture is configurable per model (`AuditRule`, NABH DAC.2.c). |

## Encryption at rest

| Requirement | Status |
|---|---|
| Patient identifiers and contact details encrypted, keys from the environment | **In place for:** mobile, alternate mobile, address, national ID number, insurance policy number, attendant phone, document notes, diagnosis. Keys come from `FIELD_ENCRYPTION_KEY(S)` / `_V2` in the environment; production refuses to start with the development keys; rotation is supported. Exact-match lookups use a separate keyed hash (`FIELD_HASH_KEY`). |
| Name, date of birth, email | **Gap.** Stored in plain text. They identify a person under DPDP. Encrypting names needs a search design (blind-index or tokenised search) because the front desk searches by name. |
| Database / backups | Full-disk or volume encryption of the server and of `backups/` is the hospital's responsibility on-premise; state it in the deployment agreement. |

## DPDP Act 2023

| Area | Status |
|---|---|
| Rights of the data principal | **In place:** `DataRightsRequest` (access, correction, erasure), grievances (`GrievanceTicket`), nominees (`Nominee`). |
| Consent | **Partly.** Clinical consent records and per-channel communication consent / opt-out exist. **Gap:** no single consent notice record tying each processing purpose to the patient's consent and withdrawal, as section 6 expects. |
| Purpose limitation / retention | **Partly.** `RetentionPolicy` covers backups and audit-log archiving. **Gap:** no per-category retention schedule for patient data, and erasure requests need a documented rule for records that clinical-records law requires the hospital to keep. |
| Breach notification | **Not verified.** No workflow found for notifying the Data Protection Board and affected people. |
| Data stays in the deployment | **In place.** Logs go to the console only (no external log service); AI uses a local Ollama model; on-premise, WhatsApp / IVR / ABDM connect only when licensed and configured, otherwise stubs. Remaining outbound path: SSO to the hospital's own identity provider, when configured. |

## NABH Digital Health Standards

Several NABH HIS/EMR controls are referenced in the code (audit capture rules
DAC.2.c, password and lockout policy DOM.4, duplicate patient detection
AAC.1.f, signed clinical documents COP.1.e). **Gap:** there is no maintained
mapping from each NABH standard to the implementing feature and its test; the
source standard is in `docs/nabh/`. Build that matrix before an assessment.

## Tenancy (SaaS)

Hospitals share one PostgreSQL database, separated by a `hospital` column on
every record and enforced in the application (`TenantScopedViewSetMixin`,
tenant-scoped managers, cross-tenant tests). Kept deliberately (decision of 10
Oct 2026). **Gap to consider:** PostgreSQL row-level security as a second,
database-enforced layer.
