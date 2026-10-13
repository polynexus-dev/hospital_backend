# ISO/IEC 27001:2022 readiness

**NABH Digital Health Standards for HIS/EMR, IMS.3.a:** *"The system complies with ISO 27001:2022 information security standards."*

ISO 27001 certifies an **organisation's** information security management system (ISMS), not a product. The software can only supply the technical controls. Certification needs Polynexus (and, for SaaS, the hosting set-up) to run an ISMS and pass an audit by an accredited certification body. This document:
- maps the controls the product already provides;
- lists what the company still has to put in place;
- describes the path to certification.

It replaces the older illustrative table in `docs/SECURITY_COMPLIANCE.md` §6.3.

## 1. Annex A technological controls the product provides

| Annex A control | What the product does | Evidence |
|---|---|---|
| A.5.15 Access control; A.5.18 Access rights | Role templates per job, an admin-set permission ceiling per hospital, every query limited to the user's hospital | `accounts` role templates; tenant-isolation tests in every app |
| A.5.17 Authentication information | Password policy (length, history, expiry) and account lockout | `governance` tests `policy_rejects_weak_passwords`, `account_locks_after_threshold` |
| A.8.2 Privileged access rights | Hospital-admin roles locked; platform staff can enter another hospital only with a stated reason, which is logged; break-glass emergency access logged for review | `test_switch_hospital…`, `EmergencyAccessLog` |
| A.8.3 Information access restriction | Clinical detail needs a clinical permission; patient portal tokens can't reach staff endpoints | `test_portal_login_covers_family_and_blocks_staff_api` |
| A.8.5 Secure authentication | Two-factor authentication (several authenticator devices), single sign-on (Microsoft Entra ID / Google Workspace, OIDC), login rate limiting | `accounts/test_sso.py`, 2FA tests |
| A.8.7 Protection against malware | Uploads restricted by type and size; served only to authorised users | Document upload validators |
| A.8.8 Technical vulnerabilities | `pip-audit` and `npm audit` block CI on high-severity findings | `.github/workflows/ci.yml` |
| A.8.9 Configuration management | Settings from environment only; production refuses to start with development keys; containers run as non-root | `test_prod_settings_reject_the_dev_*` |
| A.8.10 Information deletion | Soft delete with a grace period, then purge; DPDP erasure requests | `purge_expired_soft_deleted_records` task |
| A.8.11 Data masking | Encrypted fields shown only to permitted roles; redacted in audit logs | `test_patient_changes_are_audited_without_leaking_encrypted_values` |
| A.8.12 Data leakage prevention | Data exports need permission and are audit-logged | `data_export_writes_an_audit_log_entry` |
| A.8.13 Information backup | Scheduled encrypted backups, tested restore, expiry purge | `test_backup_and_restore_roundtrip` |
| A.8.15 Logging | Append-only audit log (database triggers block edits), field-level clinical change history, daily log files kept 180 days | `auditlog_*` tests, `daily_log_files_and_180_day_purge` |
| A.8.16 Monitoring activities | Failed logins, lockouts and access-denied events logged; licence usage reports | `forbidden_request_is_logged_as_access_denied` |
| A.8.20–A.8.21 Network security | HTTPS only in production (redirect, HSTS, secure cookies); CORS restricted; request bodies additionally encrypted end to end (ECDH + AES-256-GCM) | `test_prod_settings_serve_only_over_https`; `docs/API_ENCRYPTION_GUIDE.md` |
| A.8.24 Use of cryptography | AES-256-GCM field encryption with key rotation; keyed HMAC search indexes; signed offline licences (private key kept only on the SaaS server) | `apps/core/encryption.py`; licensing tests |
| A.8.25–A.8.29 Secure development and testing | Reviewed pull requests, about 1,000 automated tests, cross-browser suite, test data separate from production | `SOURCE_CODE_MANAGEMENT.md` |
| A.8.32 Change management | Release notes shown to users; migrations tested on PostgreSQL before release | `dom_2_a_release_notes_reach_every_user` |
| A.5.24–A.5.28 Incident management | Incident register with CERT-In (6 hours) and DPDP (72 hours) deadlines, overdue alerts, closure only with root cause | `governance/test_incidents_and_logs.py` |
| A.5.34 Privacy and PII protection | DPDP consent register, encrypted identifiers, data-principal requests | `DpdpConsentCard`; consent tests |

## 2. What Polynexus must put in place (organisational, people, physical)

Software can't supply these. They're the work between today and an audit:

1. **ISMS scope and policy:** for example, "development, operation and support of the Polynexus hospital suite, SaaS and on-premise". This needs top-management sign-off.
2. **Risk assessment and treatment plan,** and a **Statement of Applicability** covering all 93 Annex A controls. Section 1 above covers most of the technological ones.
3. **Policies:** acceptable use, access control, cryptography and key management, supplier security (AWS, Razorpay, SMS/WhatsApp providers, HoduPBX), secure development (`SOURCE_CODE_MANAGEMENT.md`), backup, incident response, business continuity.
4. **People controls:** background checks, confidentiality agreements, security awareness training with attendance records (the HRM training module can record these), and an off-boarding checklist.
5. **Asset inventory:** servers, laptops, repositories, keys and secrets (and who holds each one).
6. **Supplier management:** collect AWS's ISO 27001 certificate and the payment gateway's PCI-DSS certificate; review them every year.
7. **Business continuity:** written RPO/RTO (see `SUPPORT_SLA.md` §3) and a recorded restore drill every quarter.
8. **Internal audit and management review:** at least one full cycle before the certification audit.

## 3. Path to certification

| Step | Typical time |
|---|---|
| Gap assessment against this document with a consultant or the certification body | 2–4 weeks |
| Write the policies and run the risk assessment; produce the Statement of Applicability | 1–2 months |
| Run the ISMS and collect records (training, access reviews, restore drills, internal audit) | 3 months minimum |
| Stage 1 audit (documents) and Stage 2 audit (operation) by an accredited body | 1–2 months |

Until the certificate is issued, NABH assessors can be given this document, the evidence pack (`EVIDENCE_PACK.md`) and the hosting provider's ISO 27001 certificate. The matrix keeps IMS.3.a as **Organisational** until the certificate exists.
