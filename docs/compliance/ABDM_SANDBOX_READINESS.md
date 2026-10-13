# ABDM sandbox certification: readiness and plan

| Part | What it covers |
|---|---|
| 1. Where we stand | What is built, what isn't, in one table |
| 2. The certification path | The steps NHA expects, in order |
| 3. Work still to build | Engineering items, with size and order |
| 4. What the hospital or company must do | Registrations, audits and paperwork that aren't code |
| 5. How we will test in the sandbox | The sequence of sandbox tests |
| 6. Where the code is | Files and settings |

ABDM (Ayushman Bharat Digital Mission) integrations are certified by the National Health Authority (NHA) through its sandbox before production credentials are issued. **Confirm the current steps on the ABDM sandbox portal before starting**: NHA updates the milestones, API versions and audit requirements from time to time.

## Part 1: Where we stand

ABDM certification is usually done in three milestones:

| Milestone | Means | Status in our product |
|---|---|---|
| **M1: ABHA** | Create or verify a patient's ABHA number and link it to the hospital's patient record | **Partly built.** Verification by Aadhaar OTP, mobile OTP or existing ABHA, and linking to the UHID, are built (`AbhaLink`). Creating a new ABHA from inside our app is not built. Field names are best-effort and must be checked against live sandbox responses. |
| **M2: Health Information Provider (HIP)** | The hospital shares records out: patients find and link their records (care contexts), approve consent, and the hospital pushes encrypted FHIR records | **Not built.** FHIR document bundles are built (prescription, lab report, imaging report, discharge summary, OP consultation; ABHA included). Missing: care-context discovery and linking callbacks, consent notification handling, and the encrypted data push (ECDH key exchange). |
| **M3: Health Information User (HIU)** | The hospital asks for a patient's records held elsewhere, with their consent, and receives them | **Partly built.** Consent requests and status checks are built (`ConsentRequest`). Receiving the records (an asynchronous, encrypted push to our callback) is not built. |
| NHCX (insurance claims) | Claims through the National Health Claims Exchange | **Partly built.** `NHCXTransaction` and its API exist; the live gateway is a stub until onboarding. |

Today every ABDM call goes to a **stub** gateway unless `ABDM_GATEWAY=real` and sandbox credentials are set, so nothing has yet been run against a live ABDM response.

NABH elements this affects: AAC.1.e (ABHA capture), AAC.3.n / AAC.4.m / AAC.6.f / COP.1.o (records linked to ABHA: bundles built and tested), COP.1.p (access to past records through ABHA: partial), IMS.1.b–c (extended and advanced FHIR profiles: built and tested, 2026-10-13), IMS.1.d (NHCX: partial until onboarding).

## Part 2: The certification path

1. **Register** on the ABDM sandbox as the integrator, and get sandbox client credentials.
2. **Register the facility** in the Health Facility Registry (HFR) and the doctors in the Healthcare Professionals Registry (HPR), as needed for HIP/HIU IDs.
3. **Build and self-test** each milestone against the sandbox (Part 5).
4. **Functional testing** by NHA's team in the sandbox: they run the scenarios and check the screens and responses.
5. **Security audit:** ABDM asks for a web-application security audit report from a CERT-In-empanelled auditor before production. Check the current requirement and plan the audit early: findings take time to fix.
6. **Production** credentials and go-live, with the facility's production IDs.

## Part 3: Work still to build

In the order to do it:

| # | Item | Why | Size |
|---|---|---|---|
| 1 | Run M1 against the sandbox and fix field names | Our ABHA calls are built from the published API collection, not live responses | Small |
| 2 | ABHA creation (not only verification) | Patients without an ABHA should be able to get one at registration | Medium |
| 3 | Gateway callback endpoints (signed requests from ABDM) | ABDM's flows are push-based: it calls our `on-init`, `on-status`, `on-notify` and similar endpoints | Medium |
| 4 | HIP care-context discovery and linking | M2: patients link their visits at our hospital to their ABHA | Medium |
| 5 | Encrypted data push (ECDH/X25519 with the published encryption spec) | M2 and M3: health records travel encrypted end to end | Large |
| 6 | HIU data receipt and display | M3: show records fetched from other providers | Medium |
| 7 | ~~Extended and advanced FHIR profiles~~ **Done 2026-10-13:** Immunization, Wellness, Health Document and Invoice records (`/abdm-fhir/<immunization\|wellness\|document\|invoice>/<id>/`). Still run them through the NRCeS validator in the sandbox | NABH IMS.1.b/c | Done |
| 8 | NHCX live gateway | Insurance claims (FPM.4.i, IMS.1.d) after NHCX onboarding | Medium |

## Part 4: What the hospital or company must do

These are not software features, and certification can't proceed without them:

- The sandbox account and credentials, kept secret (they go into the server's `.env`, never into code).
- HFR registration of each hospital, and HPR IDs for the doctors who will sign records.
- A public HTTPS address for the callback endpoints (ABDM must be able to reach them).
- The security audit by a CERT-In-empanelled auditor, and fixing its findings.
- Patient-facing consent wording for ABDM sharing: the consent register's **ABDM sharing** notice (Security & Governance → Consent notices).
- A named person for NHA's communication, and for incidents.

## Part 5: How we will test in the sandbox

1. Set `ABDM_GATEWAY=real`, `ABDM_BASE_URL`, `ABDM_CLIENT_ID`, `ABDM_CLIENT_SECRET` and `ABDM_HIP_ID` on a test server (never on a hospital's production server).
2. **M1:** verify a sandbox test ABHA by mobile OTP and by Aadhaar OTP; link it to a test patient; check the ABHA appears in the patient record and in the FHIR bundles.
3. **M3 consent:** raise a consent request for a sandbox ABHA, approve it in the sandbox PHR app, check our status changes to granted.
4. **M2:** discover and link care contexts from the sandbox PHR app; approve a consent; push a prescription, lab report and discharge summary; confirm the sandbox decrypts and validates them.
5. **M3 data:** receive another provider's records and show them in the patient's record.
6. Keep the request IDs and screenshots of each step: NHA's functional testing asks for them.

## Part 6: Where the code is

| Area | Location |
|---|---|
| Gateway client (stub and real) | `Backend/apps/abdm/gateway.py`: its docstring lists exactly what is grounded and what is not |
| ABHA link, consent requests, record fetch, NHCX | `Backend/apps/abdm/models.py`, `views.py` |
| FHIR document bundles | `Backend/apps/integrations/abdm_fhir.py` |
| Tests | `Backend/apps/abdm/tests.py`, `Backend/apps/integrations/test_abdm_fhir.py`, `Backend/apps/quality/test_nabh_core.py` (imaging bundle carries the ABHA) |
| Settings | `ABDM_GATEWAY`, `ABDM_BASE_URL`, `ABDM_CLIENT_ID`, `ABDM_CLIENT_SECRET`, `ABDM_HIP_ID`, `ABDM_TIMEOUT_SECONDS` |
