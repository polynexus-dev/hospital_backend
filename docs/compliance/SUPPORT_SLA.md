# Maintenance and support service levels

**NABH Digital Health Standards for HIS/EMR, DOM.2.b:** *"The HIS/EMR vendor provides maintenance and user support in a timely manner with clearly defined service level agreements (SLAs)."*

| | |
|---|---|
| Provider | Polynexus |
| Applies to | Hospitals on the SaaS service, and on-premise hospitals with an active licence |
| Status | **Proposed.** Confirm the hours, response times and contacts below, then attach this document to every hospital contract. |

## 1. How hospitals reach support

| Channel | Use for |
|---|---|
| In-app support ticket (Help → Raise a ticket) | Everything. A ticket gets a number and an audit trail |
| Support phone / WhatsApp: *(fill in)* | Priority 1 and 2 issues, after raising the ticket |
| Email: *(fill in)* | Account and billing questions |
| In-app Help Centre (`/help`) | Self-service guides, searchable |

Support hours *(proposed)*: 09:00–21:00 IST, Monday to Saturday. **Priority 1 issues are covered 24 × 7.**

## 2. Priorities and targets

| Priority | Meaning | Examples | First response | Workaround or fix |
|---|---|---|---|---|
| **P1 Critical** | The hospital can't treat or register patients, or patient data may be exposed | Whole system down; login impossible for everyone; suspected data breach | 30 minutes, 24 × 7 | Workaround within 4 hours; fix within 24 hours |
| **P2 High** | A major module is unusable, but there is a manual fallback | Billing can't print; lab results not saving; pharmacy dispensing blocked | 2 business hours | Within 1 business day |
| **P3 Medium** | Partial loss of a feature, or a wrong result with an easy workaround | A report filter is wrong; one template misprints | 1 business day | Within 5 business days, or in the next release |
| **P4 Low** | A question, a cosmetic issue or a feature request | Spelling, layout, "how do I" | 2 business days | Next planned release, or answered |

Suspected breaches (P1) also start the breach procedure in the incident register: notify CERT-In within **6 hours** and the Data Protection Board / affected people within **72 hours**, as the in-app incident workflow tracks.

## 3. Availability (SaaS)

- **Target:** 99.5% monthly availability *(proposed)*, not counting announced maintenance.
- **Planned maintenance:** announced at least 48 hours ahead in the app, and done between 23:00 and 05:00 IST.
- **Backups:** every day. Restore point (RPO) ≤ 24 hours; restore time (RTO) ≤ 8 hours *(proposed)*. Restores are rehearsed every quarter.

For on-premise installs, the hospital runs the server. Polynexus supports the software remotely, with the hospital's permission, through the same priorities.

## 4. Maintenance included

- Bug fixes and security patches for the current release and the one before it.
- Updates for regulatory changes (NABH, ABDM, DPDP, GST) within a reasonable time after they are published.
- New releases with release notes shown to every user in the app (NABH DOM.2.a).
- On-premise: a new bundle and licence renewal before the licence expires (the app warns 30 days ahead).

## 5. Escalation

1. Support engineer, who owns the ticket.
2. Support lead, for any P1, or a P2 that misses its target.
3. Engineering lead, for a P1 still unresolved after 4 hours.
4. Director, *(fill in name)*, if the hospital asks to escalate.

## 6. Reporting

Every month, Polynexus sends each hospital:
- tickets opened, closed and still open, by priority;
- the share that met each response and resolution target;
- availability achieved (SaaS);
- any P1 incident with its root cause.

The SaaS console's ticket list (assignment, status, timestamps) is the source for this report.

## 7. Exclusions

The targets don't apply to:
- problems caused by the hospital's own network, hardware or third-party services, such as SMS and WhatsApp providers, the payment gateway or ABDM;
- changes made by the hospital outside the product;
- requests for new features, which are quoted separately.
