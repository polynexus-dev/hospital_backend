# ISO 82304-1 health software: product self-assessment

**NABH Digital Health Standards for HIS/EMR, IMS.3.b:** *"The system adheres to ISO 82304 health software standards."*

ISO 82304-1:2016 sets product safety and security requirements for health software running on general-purpose computers. Its central requirement is a **product validation** with documented requirements, plus clear **accompanying documents** (identification, intended use, instructions, release and support information). This self-assessment records how the Polynexus hospital suite meets each clause. Formal conformity needs either:
- an independent assessment; or
- a signed manufacturer's declaration backed by the records below.

| | |
|---|---|
| Product | Polynexus hospital suite (HMS, CRM, ERP, MIS): SaaS and on-premise |
| Version assessed | Backend and frontend `main` as of 2026-10-13; on-premise bundle 1.0.x |
| Manufacturer | Polynexus |
| Status | **Self-assessment.** To be signed by the product owner; independent assessment not yet done |

## 1. Intended use and safety classification

- **Intended use:** the administrative and clinical record-keeping, ordering, billing and reporting work of hospitals and clinics in India, used by trained hospital staff and, through the patient portal, by patients.
- **Not intended for:** making diagnoses or treatment decisions on its own. Clinical decision support (drug interactions, allergy and contraindication alerts, early-warning scores) advises a clinician, who stays responsible; overriding a critical alert needs a recorded reason. The product does not drive medical devices; it only reads data from them.
- **Medical-device status:** under the Medical Devices Rules 2017 as they stand, it is treated as non-device health software. Re-check if CDSS features begin to make autonomous decisions.

## 2. Clause-by-clause

| ISO 82304-1 clause | Requirement | How it's met | Evidence |
|---|---|---|---|
| 4 Product requirements | Documented requirements covering intended use, safety, security, privacy, usability, interoperability | NABH HIS/EMR standard used as the requirement set (223 elements), traced element by element to code and tests | `docs/nabh/COMPLIANCE_MATRIX.md` (every implemented element has a tagged automated test) |
| 4.2 Health and safety requirements | Hazards identified and controlled | Patient-safety controls: duplicate-order warnings, allergy, interaction and contraindication blocking, high-risk drug double check, wrong-patient blocking at eMAR, critical-result alerts, positive patient ID by UHID / barcode | Tests in `clinical`, `pharmacy`, `laboratory`, `nursing` |
| 4.3 Security and privacy | Protection of health data | Encryption at rest, access control, audit trail, DPDP consent, breach workflow | `ISO27001_READINESS.md` §1 |
| 4.4 Interoperability | Standards-based exchange | ABDM FHIR R4 document bundles (OP consultation, prescription, diagnostic report, discharge summary, immunization, wellness, health document, invoice), HL7 v2 analyser import, DICOM, ICD-10, LOINC, SNOMED CT | `apps/integrations/abdm_fhir.py`, `test_abdm_fhir.py` |
| 5 Software life-cycle processes | Life-cycle per IEC 62304-style processes (development, maintenance, configuration, problem resolution) | Reviewed changes, CI gates, release process, support SLA, incident register | `SOURCE_CODE_MANAGEMENT.md`, `SUPPORT_SLA.md` |
| 6 Product validation | Validation that the product meets its requirements for its intended use | About 1,000 automated backend tests, frontend unit tests, cross-browser suite; NABH matrix rebuilt from the tags on every change | CI runs; `COMPLIANCE_MATRIX.md` |
| 7 Product identification | Name, version, manufacturer visible | Version shown in the app and licence card; release notes per version | Settings → Licence; release notes |
| 7 Accompanying documents | Intended use, installation, operating instructions, technical description, support contacts | Client delivery guide, trial install guide, on-premise deployment guide, how-it-works guide, in-app Help Centre | `docs/CLIENT_DELIVERY.md`, `docs/TRIAL_INSTALL_WINDOWS.md`, `docs/ONPREM_DEPLOYMENT_GUIDE.md`, `docs/HOW_IT_WORKS.md` |
| 8 Post-market activities | Collect feedback and problems; inform users of safety issues | Support tickets, incident register, release notes reaching every user, licence usage reports | `SUPPORT_SLA.md`; `governance` app |

## 3. Gaps to close before signing a declaration

1. **Risk management file:** a written hazard list with risk controls and the residual risk for each (the controls exist in code; the file collecting them doesn't). Use ISO 14971 as a guide.
2. **Usability evaluation:** record at least one formative usability session per main user group (front desk, doctor, nurse, pharmacist, patient) and the changes made as a result.
3. **Validation report:** for the release being declared, a short report: test totals, failures and how they were resolved, open known issues.
4. **Instructions for use:** one consolidated user manual (today the guidance is spread across the Help Centre and the delivery documents).
5. **Signed declaration:** by the product owner, naming the version.

The matrix keeps IMS.3.b as **Organisational** until the declaration is signed or an independent assessment is done.
