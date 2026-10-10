# Security & Compliance Posture — Polynexus Hospital CRM

**Document type:** Internal security control inventory & gap analysis
**Scope:** `Backend/` (Django REST API) and `Frontend/` (React SPA)
**Frameworks referenced:** ISO/IEC 27001:2022 (Annex A), India's Digital Personal Data Protection Act 2023 (DPDP Act) + draft DPDP Rules 2025, IT Act 2000 Sec. 43A / SPDI Rules 2011, NABH accreditation standards (Management of Information / Patient Rights chapters)
**Prepared:** 2026-08-14 · **Updated:** 2026-09-22 (C1 and H1 — previously marked fixed on 2026-08-14 — were found silently reintroduced by an unrelated commit on 2026-09-11 and have been re-fixed; a new finding, H8, was discovered during the same pass. See §9.)
**Classification:** Internal — Confidential. This document names concrete, unpatched security gaps in a system handling patient PHI and insurance data. Do not share externally or commit to a public repository without redacting Section 4 (Gap Analysis).

> This document was produced by reading the actual codebase (file:line citations throughout), not by assuming best practice was followed. Anything marked **NOT FOUND** was searched for and confirmed absent as of this date. Re-verify before relying on this for a formal audit — code changes will make specific line numbers stale.

---

## 1. Purpose & Scope

This system processes:
- **Personal Data**: patient names, contact details, demographics
- **Sensitive Personal Data / SPDI** (per SPDI Rules 2011) and **Sensitive Personal Data** (per DPDP Act framing): national ID numbers (Aadhaar/PAN/Passport), health records, diagnoses, prescriptions, medical documents
- **Financial data**: insurance policy numbers, claim amounts, settlement amounts (via the TPA — Third Party Administrator — module)

This makes the platform a **Data Fiduciary** under the DPDP Act 2023, a handler of **Sensitive Personal Data or Information (SPDI)** under IT Act Sec. 43A, and — because it stores clinical/medical records on behalf of hospitals — subject to **NABH** information-security expectations for accredited facilities using it.

## 2. Data Classification Summary

| Data category | Examples | Where stored | Current at-rest protection |
|---|---|---|---|
| Identity/PII | name, phone, email, address | `apps/patients/models.py` | Plaintext (DB-level access control only) |
| National ID (SPDI) | Aadhaar/PAN/Passport number | `Patient.national_id_number` (`apps/patients/models.py`) | **Encrypted at rest** (`EncryptedTextField`, fixed 2026-08-14) |
| Health data (PHI) | diagnosis, symptoms, medications, lab orders | `apps/patients`, prescriptions API | Plaintext |
| Insurance/financial | policy number, claim amount, settled amount | `Patient.insurance_policy_number` and `PreAuthRequest.policy_number` (`apps/patients/models.py`, `apps/tpa/models.py`) — **encrypted at rest** (fixed 2026-08-14, exact-match search preserved via a blind index); `Claim.billed_amount`/`settled_amount` are plain `DecimalField`s, not identifiers, and were never in scope for C2 |
| Documents | uploaded reports, ID proofs, consent forms | `media/patient_documents/%Y/%m/` (local filesystem) | Plaintext files; extension/size validated on upload (fixed 2026-08-14), **still no access-controlled serving path** |
| Credentials | user passwords | `apps/accounts` | Django PBKDF2-SHA256 hash (default, not overridden) — appropriate |

---

## 3. Implemented Security Controls

### 3.1 Identity & Access Management (Authentication)
- **Mechanism**: JWT via `djangorestframework-simplejwt`, `SessionAuthentication` also enabled — `config/settings/base.py:177-180`.
- **Token lifetimes**: access 8h, refresh 7d, rotation on refresh — `base.py:220-227`.
- **Login throttling**: dedicated `"login": "5/minute"` scope on the login view (`apps/accounts/views.py:35`), plus general `AnonRateThrottle` (60/min) and `UserRateThrottle` (300/min) — `base.py:192-213`.
- **Password policy**: standard Django `AUTH_PASSWORD_VALIDATORS` (length, similarity, common-password, numeric) — `base.py:137-142`.
- **Password hashing**: Django default PBKDF2-SHA256, not weakened.
- **Placeholder-secret guard**: production settings refuse to boot if `SECRET_KEY` still contains `"change-me"` or `"insecure"` — `config/settings/prod.py:16-17`. Good fail-closed pattern.

### 3.2 Authorization / RBAC & Multi-Tenancy
- **Role model**: `Role` (`apps/accounts/models.py:35-78`) is hospital+department scoped, backed by a Django `Group`; `User` carries `hospital`, `department`, `role` foreign keys (`models.py:81-121`).
- **Global permission class**: `RoleBasedModelPermissions` (`apps/core/permissions.py:11-52`), applied with `IsAuthenticated` as the DRF default (`base.py:181-184`) — every endpoint requires auth + Django model permissions by default.
- **Multi-tenant isolation**: contextvar-based current-hospital scoping (`apps/core/tenancy.py`), a `TenantManager` that auto-filters querysets (`apps/core/managers.py:12-28`), and a `TenantScopedViewSetMixin` that filters by `request.user.hospital_id` and stamps `hospital` on create (`apps/core/viewsets.py:50-65`). In-code comments document two previously-fixed cross-tenant leak bugs, and cross-tenant isolation is explicitly unit-tested (e.g. `apps/tpa/tests.py:288-402`). This is a materially strong control for a multi-hospital SaaS — it's the difference between one hospital's patient data being architecturally unreachable by another vs. merely policy-restricted.
- Staff-only cross-tenant access (`switch_hospital`) is gated to `is_staff` (`apps/accounts/views.py:54-80`), also noted as a fix for a prior privilege-escalation bug.

### 3.3 Transport & Network Security
- **Production-only hardening** (`config/settings/prod.py`): `SECURE_SSL_REDIRECT=True`, `SESSION_COOKIE_SECURE=True`, `CSRF_COOKIE_SECURE=True`, `SECURE_HSTS_SECONDS` = 30 days with `INCLUDE_SUBDOMAINS` + `PRELOAD`, `SECURE_PROXY_SSL_HEADER` set for reverse-proxy TLS termination, `SECURE_CONTENT_TYPE_NOSNIFF=True`.
- `XFrameOptionsMiddleware` active (`base.py:76`) → clickjacking protection (`X-Frame-Options: DENY` by default).
- `CSRF_TRUSTED_ORIGINS` wired to the same origin list as CORS in production (`prod.py:36`), needed because `SessionAuthentication` stays enabled alongside JWT.

### 3.4 Application-Layer Injection Protection
- No raw SQL in application code — confirmed via repo-wide search for `.raw(`, `.extra(`, `cursor.execute` (only hit is inside a test asserting audit-log immutability, `apps/core/tests.py:331`). All data access goes through the Django ORM.
- Standard DRF `ModelSerializer` validation throughout, with custom validators where needed (e.g. `PatientSerializer.validate_guardian`, `apps/patients/serializers.py:23-26`, preventing a patient being their own guardian).

### 3.5 Audit Logging
- **`AuditLog` model** (`apps/core/models.py:75-117`) is **append-only at the model level** — `save()` on update and `delete()` are overridden to raise, so even a compromised application code path can't rewrite or erase history (`models.py:111-117`).
- **`AuditMiddleware`** (`apps/core/middleware.py:34-63`) automatically logs every mutating request (`POST/PUT/PATCH/DELETE`, excluding `/admin/`): actor, hospital, HTTP method, path, status code, and client IP.

### 3.6 Secrets & Configuration Management
- Configuration loaded via `django-environ` from `.env` (`base.py:10,15-18`), never hardcoded (aside from the intentionally-placeholder dev default).
- `.gitignore` excludes `.env` (line 9) and the patient-documents media directory (line 14) from version control.
- Settings are split `base.py` / `dev.py` / `prod.py` / `test.py`, so dev-only conveniences (e.g. `DEBUG=True`) can't silently leak into production without an explicit env override.

### 3.7 CI / Dependency Hygiene (baseline present, not yet enforcing)
- Backend CI runs `pip-audit` against `requirements.txt` and `python manage.py check --deploy` on every push/PR (`.github/workflows/ci.yml`).
- Frontend CI runs `npm audit --audit-level=high` (`Frontend/.github/workflows/ci.yml:31-36`).
- Dependencies are version-pinned in both `requirements.txt` and `package.json`/lockfile.

### 3.8 Frontend Client Security
- No `dangerouslySetInnerHTML`, `innerHTML`, or `eval` usage anywhere in `Frontend/src` — confirmed by repo-wide search. React's default JSX escaping is the operative XSS control and there are currently no raw-HTML injection points to defeat it.
- Access token kept **in memory only** (Zustand store), not persisted — `src/store/auth.ts:16-18`.
- No secrets shipped to the client bundle: the only `VITE_*` variable is a non-secret API base URL.
- No source maps in the production build (confirmed no `.map` files under `Frontend/dist/assets`).

---

## 4. Gap Analysis

Findings are ordered by severity. "Evidence" cites the exact file/line confirmed during this review.

### Critical

| # | Finding | Evidence | Risk | Status |
|---|---|---|---|---|
| C1 | ~~`CORS_ALLOW_ALL_ORIGINS = True` was hardcoded, unconditionally, in the shared base settings used by dev, test, *and* production.~~ | `Backend/config/settings/base.py:247` (original), reintroduced at line 336 by commit `5da8314` (2026-09-11) | Overrode the `CORS_ALLOWED_ORIGINS` allow-list entirely — the API sent `Access-Control-Allow-Origin: *` in production. | **Fixed 2026-08-14, silently reintroduced 2026-09-11, re-fixed 2026-09-22** — see §9. A regression guard test (`test_cors_allow_all_origins_is_not_enabled`, `apps/core/tests.py`) now fails the suite if this line comes back a third time. |
| C2 | National ID numbers (Aadhaar/PAN/Passport) and insurance policy numbers were stored as **plaintext** database columns with no field-level encryption. | `apps/patients/models.py` (`Patient.national_id_number`, `.insurance_policy_number`), `apps/tpa/models.py` (`PreAuthRequest.policy_number`) | Direct DB access (backup theft, misconfigured replica, insider access, SQL-layer compromise) exposes SPDI/PHI in cleartext. DPDP Act §8(5) and SPDI Rules 2011 expect "reasonable security safeguards" — encryption of sensitive identifiers at rest is the standard baseline read of that requirement; a policy number joined to a named patient and a claim amount additionally implies medical-treatment information, so it isn't just "financial data." | **Fixed 2026-08-14** — all three fields now use `apps.core.encryption.EncryptedTextField` (Fernet via `MultiFernet`, key(s) from `FIELD_ENCRYPTION_KEYS`; prod fails closed on the checked-in dev key, same pattern as `SECRET_KEY`). Existing rows were re-encrypted in place by 3-step migrations (`patients` `0006`-`0008`, `tpa` `0003`-`0005`). `PreAuthRequest.policy_number` needed a search feature (`?policy_number=` / admin `search_fields`) preserved, so it's paired with `policy_number_lookup`, a deterministic HMAC-SHA256 blind index (`apps.core.encryption.compute_blind_index`, keyed by `BLIND_INDEX_KEY`) — exact-match lookup still works, substring/partial search does not (a deliberate, documented trade-off of moving off plaintext `icontains`). `Claim` has no `policy_number` field (only `claim_number`, out of this finding's original scope). |
| C3 | Patient document uploads (`Document.file`, `apps/patients/models.py:93`) had **no file type, MIME, or size validation**, and there is still no access-controlled application view serving them — files are left to whatever serves `MEDIA_ROOT` at deploy time. | `apps/patients/serializers.py:39-43`; no media-serving route found in `config/urls.py` | Unrestricted upload is a malware/web-shell vector and a disk-exhaustion DoS vector; unauthenticated or under-authorized media serving risks exposing patient documents to anyone with a guessable/leaked URL. | **Partially fixed 2026-08-14** — extension allow-list (pdf/jpg/jpeg/png/doc/docx) and a 10MB size cap now enforced at the model/serializer layer (`apps/patients/models.py`, migration `0005_alter_document_file`). Access-controlled serving of already-uploaded files is still open. |

### High

| # | Finding | Evidence | Risk | Status |
|---|---|---|---|---|
| H1 | ~~`BLACKLIST_AFTER_ROTATION = True` was configured, but `rest_framework_simplejwt.token_blacklist` was not in `INSTALLED_APPS`~~, so there was no backing table — rotated refresh tokens were not actually blacklisted. | `base.py:220-227` vs. `INSTALLED_APPS` | A stolen refresh token remained valid for its full lifetime even after rotation/logout — no real revocation path existed. | **Fixed 2026-08-14, silently reintroduced 2026-09-11 (commit `5da8314` dropped the app from `INSTALLED_APPS`, the `/auth/logout/` route, and the frontend's call to it, all at once), re-fixed 2026-09-22** — same shape as before: `rest_framework_simplejwt.token_blacklist` back in `INSTALLED_APPS` (its migrations were already applied in the dev DB from before the regression, so no data was lost), `POST /auth/logout/` restored in `apps/accounts/urls.py` (simplejwt's `TokenBlacklistView`), and the frontend's `handleLogout` (`Frontend/src/app/Shell.tsx`) now calls `logoutRequest` (`Frontend/src/api/auth.ts`) best-effort before clearing local state. Covered by `test_logout_blacklists_the_refresh_token_so_it_can_no_longer_be_used` (`apps/accounts/tests.py`) — this test did not survive the regression either, which is exactly why it went unnoticed; it's back now. |
| H2 | Frontend stores the refresh token in `localStorage`. | `Frontend/src/store/auth.ts:4,18,21,27` | `localStorage` is readable by any script on the page; a single XSS bug (now or in a future dependency) becomes a 7-day account-takeover primitive, not just a session-length one. | Open — needs a decision between httpOnly-cookie storage (backend/frontend contract change) vs. a shortened refresh lifetime as an accepted tradeoff. |
| H3 | `User.is_2fa_enabled` and `User.allowed_ip_ranges` fields exist and are exposed in Django admin, but **no OTP/TOTP verification logic or IP-range enforcement exists anywhere in the codebase.** | `apps/accounts/models.py:100-101`, `apps/accounts/admin.py:17` | These read as implemented controls to anyone reviewing the data model or admin UI, but do nothing — worse for an audit than not having the fields at all, since it invites a false attestation. | Open — needs a decision to either build real MFA or remove the field. |
| H4 | No account lockout / progressive backoff on repeated failed logins — only a flat 5/minute rate throttle. No `django-axes` or equivalent in dependencies. | `requirements.txt` (absent); throttle at `apps/accounts/views.py:35` | A distributed or slow-and-low credential-stuffing attack is not meaningfully slowed. | Open. |
| H5 | No password-reset ("forgot password") flow found anywhere in `apps/accounts`. | grep across `apps/accounts` for reset/forgot patterns — no hits; only authenticated `change_password` exists (`views.py:83-93`) | Either a functional gap (users can't self-recover) or an unreviewed area — needs product confirmation either way. | Open — needs product confirmation before building. |
| H6 | ~~Docker image ran as root~~ — no `USER` directive in the Dockerfile. | `Backend/Dockerfile` | Standard container-hardening baseline violation; increases blast radius of any RCE in the app or a dependency. | **Fixed 2026-08-14** — image now creates and runs as an unprivileged `app` user. |
| H7 | No enforced TLS on the Django→PostgreSQL connection (`sslmode` not set anywhere). | `base.py:104-131`; no `sslmode` in repo | If app and DB are ever not co-located on a trusted private network, credentials and query data (including PHI) travel unencrypted. | Open — needs an env-driven `sslmode` default that doesn't break local/dev connections. |
| H8 | `apps.core.permissions.HospitalActive` (403s any request from a suspended hospital's non-staff users — the SaaS billing-suspension enforcement) was dropped from `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]` by the same 2026-09-11 commit (`5da8314`) that reintroduced C1/H1, with no other view wiring it in — it became fully dead code. | `config/settings/base.py` `DEFAULT_PERMISSION_CLASSES`; `apps/core/permissions.py:124-136` (class itself untouched) | A hospital whose subscription was suspended/non-paying (`Hospital.is_active=False`) kept full read/write access to every endpoint — the enforcement mechanism for non-payment silently stopped working. Discovered only because `apps/saas_admin/views.py:137`'s own comment ("Skips HospitalActive: a suspended hospital's users must still be able to open a ticket") still assumed it was globally active, which is what prompted checking. | **Fixed 2026-09-22** — restored to `DEFAULT_PERMISSION_CLASSES`. Covered by `test_suspended_hospitals_non_staff_user_is_locked_out` and `test_suspended_hospitals_staff_user_is_not_locked_out` (`apps/core/tests.py`). |

### Medium

| # | Finding | Evidence | Risk |
|---|---|---|---|
| M1 | `apps/core/audit.log_action()` — built for field-level before/after diffs on sensitive models — is **defined but never called anywhere** in the codebase; only referenced in docstrings. | `apps/core/audit.py:9-21`; confirmed via repo-wide grep | Actual audit trail is limited to coarse request metadata (who/when/path/status). No record of *what changed* on a patient, claim, or policy record — a real gap against NABH's expectation of a traceable record-modification history and DPDP's accountability principle. |
| M2 | No structured consent record. The only consent-related artifact is a free-text "Consent form" document upload category. | `apps/patients/models.py:86` (`CONSENT = "consent"`) | DPDP Act §6 expects consent that is specific, informed, and **verifiably withdrawable**. A scanned PDF in a document list satisfies none of that programmatically. |
| M3 | No centralized error tracking or structured `LOGGING` config — Django defaults apply. No Sentry or equivalent in dependencies. | Confirmed absent in `base.py`/`dev.py`/`prod.py`/`test.py` and `requirements.txt` | Slower incident detection and response; weak evidentiary trail for a security investigation beyond the `AuditLog` table. |
| M4 | CI vulnerability/security gates are all **non-blocking** (`continue-on-error: true`): `pip-audit`, `manage.py check --deploy`, and frontend `npm audit --audit-level=high`. | `Backend/.github/workflows/ci.yml` (pip-audit, django-check jobs), `Frontend/.github/workflows/ci.yml:31-36` | A newly-disclosed CVE in a pinned dependency, or a Django deploy-check failure, will not block a merge — it only appears in logs for someone to notice. |
| M5 | No SAST scanning (Bandit, CodeQL, Semgrep) and no Dependabot config in either repo. | Only workflow file present in each repo is `ci.yml`; no `.github/dependabot.yml` | Dependency CVEs and common code-level vulnerability classes aren't caught automatically between manual reviews. |
| M6 | Sensitive fields (insurance policy numbers, diagnoses, medications) are rendered **unmasked** in the frontend with no redaction or reveal-on-demand pattern. | `Frontend/src/features/tpa/TPAPage.tsx:162,242-251`; `src/types/api.ts:61-62,90`; `src/api/prescriptions.ts:10-22` | Increases shoulder-surfing/screen-share exposure of PHI/financial identifiers beyond what's operationally necessary for most views. |
| M7 | `docker-compose.yml` publishes Postgres (5432) and Redis (6379) directly to the host, with hardcoded default credentials (`polynexus`/`polynexus`). | `Backend/docker-compose.yml:10-11,20-21` | Fine for local dev; a real risk if this compose file is ever reused as-is on any network-reachable host. |
| M8 | No per-role client-side route guarding — a single `ProtectedRoute` checks only "is authenticated," not role. `/admin` is reachable by any authenticated user's browser (though backend `RoleBasedModelPermissions` should still block unauthorized mutations). | `Frontend/src/App.tsx:32-50`, `src/app/ProtectedRoute.tsx:22-26` | Defense-in-depth gap — UI structure/metadata for admin features is reachable by non-admin users even if data calls are ultimately rejected server-side. |

### Low

| # | Finding | Evidence | Risk |
|---|---|---|---|
| L1 | `SESSION_COOKIE_AGE` / `SESSION_EXPIRE_AT_BROWSER_CLOSE` not explicitly set (Django default: 2-week session cookie), and `SessionAuthentication` remains an active auth class. | Not found in any settings file | Low impact since JWT is the primary auth path, but an unnecessary residual session lifetime. |
| L2 | No Content-Security-Policy on the frontend (no meta tag, no header at the hosting layer within this repo). | `Frontend/index.html`, `vite.config.ts` | No current injection point exploits this, but it removes a defense-in-depth layer against any future one (including via a compromised dependency). |
| L3 | Frontend `.gitignore` does not explicitly exclude `.env` (only `*.local`). | `Frontend/.gitignore:1-2` | Currently `.env` holds only a non-secret API URL, so no live exposure — but nothing stops a future secret being added and committed by mistake. |

---

## 5. What's Already Solid

Worth stating plainly so the roadmap below reads as "close remaining gaps," not "start from zero":

- The **multi-tenant data isolation architecture** (contextvar + queryset-level filtering + explicit cross-tenant leak tests) is a genuinely strong, secure-by-design control — this is the hardest part of a multi-hospital system to get right, and it's been treated seriously (including fixing and regression-testing two prior leak bugs).
- The **append-only `AuditLog`** model is a good pattern — tamper-resistance is built into the model layer, not just policy.
- **Production settings fail closed** on a placeholder `SECRET_KEY` rather than silently booting insecurely.
- Standard, unmodified Django/DRF primitives are used throughout (ORM-only queries, default password hashing, default CSRF/XSS middleware) — no evidence of anyone weakening a framework default to work around a problem.
- A CI/CD security baseline (dependency scanning, deploy checks, test coverage) already exists in both repos — the gap is that it doesn't gate merges yet, not that it's absent.

---

## 6. Regulatory & Standard Mapping

### 6.1 DPDP Act 2023 — key obligations and current status

| Obligation | DPDP Act reference | Status |
|---|---|---|
| Reasonable security safeguards against breach/leak | §8(5) | Solid — transport security, RBAC, CORS (C1, fixed), and PII/insurance-identifier encryption (C2, fixed) now cover the main gaps this section flagged; upload validation (C3) is partially closed, see below. |
| Notify Data Protection Board & affected principals on breach | §8(6) | **No documented breach-notification procedure or tooling found** — this is an organizational/process control, not code; needs a written incident-response plan (see §7). |
| Consent: specific, informed, verifiable, withdrawable | §6 | Gap — see M2. No structured consent record exists today. |
| Data Principal rights: access, correction, erasure, grievance redressal | §11-13 | Not evidenced in the API surface reviewed — worth a dedicated review of whether patients/data principals have any self-service or admin-mediated path to exercise these rights. |
| Data Fiduciary accountability (demonstrable compliance) | §8, general | Audit logging exists but is coarse (M1) — insufficient on its own to demonstrate who-changed-what for a regulator. |

### 6.2 IT Act 2000 Sec. 43A / SPDI Rules 2011
Health records and financial information fall squarely within the SPDI Rules' definition of sensitive personal data. The Rules' safe-harbor expectation is a documented security policy comparable to IS/ISO 27001 *and evidence it's actually followed* — the gaps in §4 (especially C1-C3) are the kind of thing that would need to be closed, and this document itself would need to be paired with a formal Information Security Policy to claim safe-harbor compliance.

### 6.3 ISO/IEC 27001:2022 — representative Annex A control mapping

| Annex A control | Area | Status |
|---|---|---|
| A.5.15 Access control | RBAC + tenant scoping | Implemented (§3.2) |
| A.5.18 Access rights | Role-based, per-hospital | Implemented, staff cross-tenant path reviewed |
| A.5.34 Privacy and protection of PII | Field-level protection of identifiers | Implemented (C2, fixed) |
| A.8.3 Information access restriction | Multi-tenant query scoping | Implemented, well-tested |
| A.8.5 Secure authentication | JWT + throttling | Partial — MFA field exists but unimplemented (H3) |
| A.8.8 Management of technical vulnerabilities | Dependency scanning | Present but non-blocking (M4, M5) |
| A.8.9 Configuration management | Env-based settings, fail-closed secret check | Implemented, incl. non-root container (H6, fixed) |
| A.8.11 Data masking | Sensitive field display | Gap (M6) |
| A.8.15 Logging | Audit trail | Partial — request-level only, no diffs (M1) |
| A.8.16 Monitoring activities | Error/security monitoring | Gap (M3) |
| A.8.20 Networks security | CORS/TLS | CORS fixed (C1); DB-connection TLS still open (H7) |
| A.8.24 Use of cryptography | Encryption at rest | Implemented — Fernet field encryption + HMAC blind index for searchable fields (C2, fixed) |
| A.8.26 Application security requirements | Upload validation, input handling | ORM/serializer validation strong; upload type/size validated (C3 partial-fixed), access-controlled serving still open |

This table is illustrative, not exhaustive — a formal ISO 27001 gap assessment should walk the full Annex A / Statement of Applicability with the organization's ISMS owner.

### 6.4 NABH considerations
NABH accreditation standards (Management of Information chapter and Patient Rights & Education chapter, in the relevant editions) expect, among other things: role-based access to patient records, a traceable audit trail of record access and amendment, secure retention of medical records for the statutorily-required period, and documented informed-consent capture. Against those expectations specifically:
- Role-based access: implemented (§3.2).
- Traceable amendment history: **partial** — request-level audit exists, field-level change history does not (M1) — this is the gap most directly relevant to a NABH information-management audit.
- Consent capture: **gap** — currently a document upload, not a structured, queryable consent record (M2).
- Retention/disposal policy: not found in code (expected — this is a records-management policy, not a code control) and should be formalized as an organizational document referencing the statutory minimum retention period for medical records.

---

## 7. Recommended Remediation Roadmap

**Immediate (before any further production patient-data exposure):**
1. ~~Remove the hardcoded `CORS_ALLOW_ALL_ORIGINS = True` (C1)~~ — **done**.
2. Add file-type/MIME/size validation to document uploads (**done**) and move file serving behind an authenticated, tenant-scoped view rather than raw static serving (C3 — **still open**).
3. ~~Register `rest_framework_simplejwt.token_blacklist`, run its migration, and add an explicit blacklist call on logout~~ (H1) — **done**.
4. ~~Add field-level encryption for national ID numbers and insurance policy numbers~~ (C2) — **done**, including `apps/tpa` `PreAuthRequest.policy_number` (search rebuilt on a blind-index column, see C2 status).

**Short-term:**
5. Move the refresh token to an httpOnly, Secure, SameSite cookie, or explicitly accept and document the localStorage tradeoff with a shortened refresh lifetime (H2).
6. Either implement real MFA/OTP behind `is_2fa_enabled`, or remove the field until it's built (H3).
7. Add `django-axes` (or equivalent) for real account lockout (H4).
8. Confirm with product whether a password-reset flow is intentionally absent; implement one with rate-limited, expiring tokens if not (H5).
9. ~~Add a non-root `USER` to the Dockerfile~~ (**done**); require `sslmode=require` (or stronger) on the DB connection (H7 — still open).
10. Make CI security gates blocking above a defined severity threshold; add Dependabot and a SAST scanner.

**Medium-term:**
11. Wire `apps.core.audit.log_action` into saves on `Patient`, `Document`, `PreAuthRequest`, `Claim`, and role/permission changes for real field-level diffs (M1).
12. Replace the free-text consent document category with a structured consent model (purpose, scope, timestamp, expiry, withdrawal) tied to the patient record (M2).
13. Add centralized error tracking (e.g. Sentry) and a documented log-retention/alerting policy (M3).
14. Add display-side masking for policy numbers/national IDs (e.g. last-4 reveal-on-demand, itself audit-logged) (M6).
15. Add a CSP at the hosting/CDN layer and per-role client-side route guarding for defense-in-depth (M8, L2).
16. Formalize the organizational documents ISO 27001 and DPDP compliance actually require independent of code: a written Information Security Policy, Data Retention & Disposal Policy, Incident Response / Breach Notification Plan, and a Data Protection Impact Assessment (DPIA) for the patient-data processing activities.

---

## 8. Notes on This Document
- Every "implemented" claim above cites a specific file and line reviewed on the date at the top of this document. Re-verify citations before using this in a formal audit — the codebase will drift.
- This is a code-level review, not a penetration test. It doesn't cover runtime/deployment configuration (actual production environment variables, reverse-proxy config, cloud provider IAM, backup encryption, physical security) — those need a separate infrastructure review to complete the ISO 27001 / NABH picture.
- Sections 6.1-6.4 map code-level findings to regulatory language for convenience; they are not a substitute for legal/compliance sign-off on DPDP Act or NABH conformance.

## 9. Remediation Changelog

| Date | Finding(s) | Change | Verification |
|---|---|---|---|
| 2026-08-14 | C1 | Removed hardcoded `CORS_ALLOW_ALL_ORIGINS = True` from `Backend/config/settings/base.py`. CORS is now governed solely by the `CORS_ALLOWED_ORIGINS` env-driven allow-list. | `manage.py check` clean; full backend test suite green. |
| 2026-08-14 | H1 | Added `rest_framework_simplejwt.token_blacklist` to `INSTALLED_APPS`; added `POST /api/v1/auth/logout/` (simplejwt's `TokenBlacklistView`) in `apps/accounts/urls.py`; frontend now calls it on logout (`Frontend/src/api/auth.ts:logoutRequest`, wired in `Frontend/src/app/Shell.tsx:handleLogout`) before clearing local state. | New test `test_logout_blacklists_the_refresh_token_so_it_can_no_longer_be_used` (`apps/accounts/tests.py`) passing; frontend typecheck and vitest suite green. |
| 2026-08-14 | C3 (partial) | Added an extension allow-list (`pdf, jpg, jpeg, png, doc, docx`) and a 10MB size cap to `Document.file` (`apps/patients/models.py`), enforced via migration `0005_alter_document_file`. Access-controlled serving of uploaded files (the other half of C3) is still open. | New tests `test_document_api_rejects_a_disallowed_file_extension` and `test_document_api_rejects_a_file_over_the_size_limit` (`apps/patients/tests.py`) passing. |
| 2026-08-14 | H6 | Dockerfile now creates and switches to an unprivileged `app` user before `CMD`. | Dockerfile reviewed; no build performed in this environment (no local Docker daemon) — recommend a build/run smoke test before next deploy. |
| 2026-08-14 | C2 (partial) | Added `apps/core/encryption.py` (`EncryptedTextField`, Fernet via `MultiFernet` for key rotation), a new `FIELD_ENCRYPTION_KEYS` setting (dev placeholder in `base.py`, fail-closed guard in `prod.py` mirroring the existing `SECRET_KEY` check), and applied it to `Patient.national_id_number` / `Patient.insurance_policy_number`. Existing rows are re-encrypted in place by a 3-step migration: `0006` widens the column, `0007` (data migration) encrypts existing plaintext values, `0008` swaps the field class to `EncryptedTextField`. `cryptography==50.0.0` added to `requirements.txt`. `apps/tpa` `PreAuthRequest.policy_number` was initially left plaintext pending a decision on its search feature — see next entry. | New tests in `apps/patients/tests.py` (ORM round-trip, raw-DB-is-ciphertext, blank-stays-blank) passing; full backend suite (412 tests) green; migration path additionally verified end-to-end against a simulated pre-existing plaintext row (legacy data correctly re-encrypted and still decrypts to the original value). |
| 2026-08-14 | C2 (completed) | Extended encryption to `apps/tpa` `PreAuthRequest.policy_number`, closing the field left open above: same `EncryptedTextField`, plus a new `policy_number_lookup` column — a deterministic HMAC-SHA256 blind index (`compute_blind_index()` in `apps/core/encryption.py`, keyed by new `BLIND_INDEX_KEY` setting, same fail-closed prod guard pattern) kept in sync in `PreAuthRequest.save()`. `PreAuthRequestViewSet.search_fields` (DRF `SearchFilter`, `icontains`) could not survive encryption, so it was replaced with a `?policy_number=` exact-match lookup against the blind index in `get_queryset()`; `PreAuthRequestAdmin.search_fields` similarly dropped `policy_number` (patient-name search remains). Net effect: exact full-number search still works, partial/substring search no longer does — a deliberate, documented trade-off, chosen because the DPDP Act / SPDI Rules "reasonable security safeguards" duty doesn't carve out an exception for a field a UX feature depends on. 3-step migration: `tpa/migrations/0003` (widen + add lookup column) → `0004` (data migration: encrypt existing rows, backfill lookup hashes) → `0005` (swap field class). | 5 new tests in `apps/tpa/tests.py` (ciphertext-in-DB, lookup recomputed on change, exact-match API search, case/whitespace-insensitive match, substring-does-NOT-match) passing; full backend suite green (145 tests across `tpa`/`patients`/`accounts`/`core`/`integrations`, only the 2 pre-existing unrelated Postgres-trigger failures under SQLite); migration path verified end-to-end against a simulated pre-existing plaintext `PreAuthRequest` row. |

**Not yet addressed** (still open, see §7): H2 (refresh token in localStorage), H3 (unimplemented MFA field), H4 (no account lockout), H5 (password-reset flow unconfirmed), H7 (DB connection TLS), and all remaining Medium/Low findings. These need product/design decisions (cookie-vs-localStorage tradeoff, MFA build-vs-remove) rather than a mechanical fix, so they were left for a deliberate follow-up rather than bundled into this pass.

| 2026-09-22 | C1, H1, H8 (regression discovered + re-fixed) | While answering a question about NABH/HIPAA/ABDM/ISO 27001 posture, re-verified this document's "Fixed" claims against the live codebase (per this document's own §8 warning that citations go stale) and found commit `5da8314` ("Merge updated backend parameters, models, permissions and security enhancements", 2026-09-11) had silently reintroduced `CORS_ALLOW_ALL_ORIGINS = True` (C1) and dropped `rest_framework_simplejwt.token_blacklist` from `INSTALLED_APPS` plus the `/auth/logout/` route and its frontend call (H1) — both alongside legitimate, wanted changes in the same commit (JWT lifetime tightening, an encryption-key naming refactor), which is presumably how the regression went unreviewed. The same commit also dropped `apps.core.permissions.HospitalActive` from `DEFAULT_PERMISSION_CLASSES` with nothing else wiring it in, silently disabling the suspended-hospital-account lockout (new finding H8 — not in the original 2026-08-14 gap analysis, since it wasn't broken yet). All three re-fixed to their previous behavior; see each finding's row in §4 for specifics. Also confirmed `rest_framework_simplejwt.token_blacklist`'s migrations were still applied in the dev DB (the regression removed the app config, not the tables), so no data was lost. | Full backend suite green (639 tests) plus 4 new regression-guard tests: `test_cors_allow_all_origins_is_not_enabled`, `test_suspended_hospitals_non_staff_user_is_locked_out`, `test_suspended_hospitals_staff_user_is_not_locked_out` (`apps/core/tests.py`), `test_logout_blacklists_the_refresh_token_so_it_can_no_longer_be_used` (`apps/accounts/tests.py`) — the same class of regression (an already-fixed finding silently coming back) should now fail CI instead of requiring another manual re-discovery. Frontend `tsc -b` clean on the two changed files (`Shell.tsx`, `api/auth.ts`); pre-existing unrelated TS errors in other files left untouched. |
| 2026-09-22 | M4 (CI gates now blocking) + one more `5da8314` casualty found | Made the CI security gates blocking as requested: backend `pip-audit` and frontend `npm audit --audit-level=high` no longer `continue-on-error`; `django-check` now runs `manage.py check --deploy` against `config.settings.prod` (not the manage.py default `dev`, which the job's own prior comment admitted "flags dev-only settings too... not a gate" — i.e. it wasn't testing anything real) scoped to `--tag security --fail-level WARNING` so it fails on genuine security warnings (e.g. a weak `SECRET_KEY`) without also failing on ~45 pre-existing, unrelated drf-spectacular schema-generation warnings this codebase already has. While wiring the prod-settings CI env, the user pasted a CI failure log for the backend `test` job: `password authentication failed for user "postgres"`. Root cause was a third casualty of the same `5da8314` commit — it had also switched `DATABASES` in `base.py` from reading a single `DATABASE_URL` to reading individual `POSTGRES_USER`/`POSTGRES_PASSWORD`/`DB_HOST` env vars, but never updated `docker-compose.yml` (whose `web`/`celery-worker`/`celery-beat` services all still only set `DATABASE_URL`) or this repo's own CI workflow to match — so **the real docker-compose deployment path was broken the same way CI was**, not just CI. Reverted `DATABASES` to `env.db("DATABASE_URL", ...)`, matching what docker-compose.yml and CI already provide; no docker-compose or CI env changes were needed once `base.py` read the variable they were already setting. **Branch-protection caveat**: making these CI jobs fail on a real problem is necessary but not sufficient for "blocking a merge" — that also requires the backend and frontend repos' GitHub branch protection rules to list `test`, `django-check`, and `build-and-test` as required status checks for `main`. `gh` CLI wasn't available in this environment to check or set that; needs to be done via each repo's Settings → Branches, or `gh` from a machine that has it. | `manage.py check --deploy --tag security --fail-level WARNING` verified locally against `config.settings.prod` with CI's exact env vars: exit 0 clean (and separately verified it correctly exits 1 when `SECRET_KEY` is weak, proving the gate actually catches something). `env.db("DATABASE_URL")` verified to parse into the correct connection dict locally. Full backend suite green (643 tests) after the `DATABASE_URL` revert. `npm audit --audit-level=high` confirmed already clean (only a pre-existing moderate-severity vitest advisory, below the threshold) before removing its `continue-on-error`. |
| 2026-09-22 | ABDM real gateway (M1 login/consent, sandbox-grounded but not live-tested) | Implemented `apps.abdm.gateway.RealABDMGateway` — a real HTTP client for ABDM's Gateway v3 API, selectable via `ABDM_GATEWAY=real` — covering `initiate_abha_verification`/`verify_abha_otp` (ABHA login-OTP flow) and `create_consent_request`/`fetch_consent_status` (HIE-CM consent init + status-poll). Session-token exchange (`POST /gateway/v3/sessions`) is grounded in ABDM's own published sandbox Postman collection (`github.com/Nirmitee-tech/abdm-v3-postman-collection`); the other endpoints' paths come from the same source but their exact request/response field names are this integration's best-effort reconstruction, **not verified against a live response** — this hospital's ABDM sandbox application was only submitted today (2026-09-22) and credentials haven't arrived. Every field access is wrapped so an unexpected shape raises a diagnosable `ABDMGatewayError` (with ABDM's raw response body logged) rather than silently returning wrong data. `fetch_health_records` deliberately still raises `GatewayNotConfigured` — ABDM's actual data-flow is async/callback-based with its own encryption scheme, not a simple HTTP call, and half-implementing that felt worse than clearly scoping it out. See `RealABDMGateway`'s docstring (`apps/abdm/gateway.py`) for the full pre-go-live checklist (facility registration, callback URL registration, credentials, live smoke test). | 18 tests in `apps/abdm/tests.py` (9 pre-existing view-level tests unaffected, 9 new) — all against mocked `requests.post`, since no live sandbox exists to test against. These prove "the gateway builds the request it claims to and parses the response shape it claims to," not "this is correct against real ABDM" — re-verify field names the day credentials arrive. `manage.py check` clean; full backend suite green (see next entry's count, run after this change). |
