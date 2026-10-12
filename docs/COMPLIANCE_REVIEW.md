# Compliance review — audit, encryption, DPDP Act 2023, NABH, CERT-In

Engineering review of what the code does today, to support (not replace) a
formal assessment. **Nothing here is a certification or a legal opinion.**
First reviewed 10 Oct 2026; updated 12 Oct 2026 after closing the gaps below.
For the reviewer-facing version see `docs/compliance/EVIDENCE_PACK.md`.

## Audit trail

| Requirement | Status |
|---|---|
| Every create / update / delete is logged: who, when, from where | **In place.** `AuditMiddleware` writes an `AuditLog` row for every mutating request (user, method, path, IP, status). The table is immutable at the database level (core migration 0003). |
| What changed on patient and clinical records | **In place (12 Oct).** `AuditedModelViewSetMixin` records old/new values where the serializer saves, so every view and workflow action is covered; all fields are compared unless narrowed. It now covers encounters, vitals, notes, diagnoses, orders, admissions, transfers, lab and radiology orders/results/reports, dispensing, OT, ICU, nursing notes, medication administration, intake/output, documents, prescriptions and the patient record. Encrypted fields are recorded as changed without values; records with encrypted fields are named by type and id, never by their content. Test: `apps/core/test_clinical_audit.py`. |
| Read access to patient records | Request-level reads of patient-record URLs are logged; per-record read capture is configurable per model (`AuditRule`, NABH DAC.2.c). |
| Retention | Audit rules can't keep logs for less than 180 days (CERT-In). |
| Legacy entries | Entries made before 12 Oct 2026 may name a record by its display text, which for prescriptions included the diagnosis. The table is immutable, so they remain. |

## Encryption at rest

| Requirement | Status |
|---|---|
| Patient identifiers and contact details | **In place:** name, date of birth, email (12 Oct), mobile, alternate mobile, address, national ID number, insurance policy number, attendant phone, prescriptions' clinical content, document notes, diagnosis. AES-256-GCM with keys from the environment; production refuses development keys; rotation supported. |
| Searching encrypted data | Exact-match keyed hashes for phone and email; for names, keyed hashes of each word's prefixes, so the front desk can type the start of any part of a name. Birth year stays plain for age-group queries. Test: `apps/patients/test_encrypted_identity.py`. |
| Other records with names | **Gap.** Enquiries (leads), blood donors and referral records still hold names in plain text. |
| Database / backups | Full-disk or volume encryption of the server and of `backups/` is the hospital's responsibility on-premise; state it in the deployment agreement. |

## DPDP Act 2023

| Area | Status |
|---|---|
| Notice and consent per purpose, withdrawal | **In place (12 Oct).** Versioned consent notices per purpose and language; patient consents recorded against the notice version; one-step withdrawal; re-consent prompted when the notice changes; marketing/reminder withdrawals switch those messages off. |
| Rights of the data principal | **In place:** `DataRightsRequest` (access, correction, erasure), grievances (`GrievanceTicket`), nominees (`Nominee`). |
| Breach notification | **In place (12 Oct).** `SecurityIncident` register with Board intimation, detailed report and patient notification recorded against deadlines; overdue alerts. Deadlines to be confirmed by counsel. |
| Retention / erasure | **Partly.** Backups and audit-log retention are configurable. **Gap:** no per-category retention schedule for patient data, and no documented rule for erasure requests that conflict with record-keeping law. |
| Children's data | **Partly.** Guardian consent is recorded by name. **Gap:** no verifiable parental-consent mechanism. |
| Data stays in the deployment | **In place.** Logs stay local; AI uses a local model; on-premise, WhatsApp / IVR / ABDM connect only when licensed and configured. |

## CERT-In Directions (April 2022)

| Direction | Status |
|---|---|
| Report incidents within 6 hours | **In place (12 Oct):** deadline tracked in the incident register, with alerts. |
| Keep logs 180 days | **In place (12 Oct):** daily log files per service, kept 180 days (on-premise `./logs`, SaaS `logs/`); audit retention floor of 180 days. |
| NTP time sync with NIC/NPL | Deployment step (RUNBOOK section 10). |

## NABH Digital Health Standards

The matrix (`docs/nabh/COMPLIANCE_MATRIX.md`) is generated from tags in the code and now separates implementing code from the tests that prove each element: 211 of 223 elements implemented, **every Core element has a tagged test**, 56 elements in total. The remaining implemented elements are listed for tagging.

## Tenancy (SaaS)

Hospitals share one PostgreSQL database, separated by a `hospital` column on
every record and enforced in the application (`TenantScopedViewSetMixin`,
tenant-scoped managers, cross-tenant tests). Kept deliberately (decision of 10
Oct 2026). **Gap to consider:** PostgreSQL row-level security as a second,
database-enforced layer.
