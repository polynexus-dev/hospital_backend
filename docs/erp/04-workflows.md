# Workflows

## CRM workflow (existing, shipped)

```mermaid
flowchart LR
    A[Lead captured\ncall / WhatsApp / web / walk-in] --> B[Enquiry created\napps.enquiries]
    B --> C{Duplicate\ndetected?}
    C -->|yes| D[Merge into\nexisting Enquiry]
    C -->|no| E[Assigned to\nCRM Executive]
    D --> E
    E --> F[Follow-up tasks\napps.automation]
    F --> G{Converted?}
    G -->|Appointment requested| H[apps.appointments\nAppointment]
    G -->|Lost| I[Lost reason recorded]
    H --> J[--- CRM/ERP boundary ---]
```

This is what exists today. `J` is the handoff point this plan's Phase 3 activates — an `Appointment` reaching its scheduled time currently just sits there; Phase 3 makes checking a patient in create an `opd.Encounter`, which is where the ERP lifecycle below begins.

## ERP patient lifecycle (new — the full spec Part 7 flow)

```mermaid
flowchart TD
    L[CRM Lead] --> ENQ[Enquiry]
    ENQ --> APR[Appointment Request]
    APR --> APPT[ERP Appointment\napps.appointments]
    APPT --> REG[Patient Registration\nUHID assigned]
    REG --> OPD[OPD Encounter\napps.opd]
    OPD --> CONS[Doctor Consultation\nvitals, diagnosis, notes]
    CONS --> INV{Investigation\nneeded?}
    INV -->|yes| LAB[Lab / Radiology order]
    LAB --> CONS
    INV -->|no| DEC{Disposition}
    CONS --> DEC
    DEC -->|OPD treatment only| RX[Prescription + OPD Billing]
    DEC -->|Admission required| ADM[Admission\napps.ipd]
    RX --> FUP1[CRM: post-visit follow-up]

    ADM --> BED[Ward / Bed allocation\napps.facilities]
    BED --> CARE[Nursing + Doctor Care\ndaily progress]
    CARE --> ANC{Ancillary needs}
    ANC -->|Lab/Radiology| LAB2[Investigation]
    ANC -->|Pharmacy| PHARM[Dispensing\napps.pharmacy]
    ANC -->|Surgery| OT[OT / Surgery\napps.ot]
    ANC -->|Critical| ICU[ICU\napps.icu]
    LAB2 --> CARE
    PHARM --> CARE
    OT --> CARE
    ICU --> CARE

    CARE --> DISCH_DEC{Ready for\ndischarge?}
    DISCH_DEC -->|no| CARE
    DISCH_DEC -->|yes| DISCH[Discharge workflow\napps.ipd.DischargeSummary]
    DISCH --> BILL[Final Billing\napps.billing]
    BILL --> PAY{Payment /\nInsurance}
    PAY --> SUMMARY[Discharge Summary\nfinalized]
    SUMMARY --> FUP2[CRM: post-discharge\nfollow-up + NPS]

    style L fill:#eef,stroke:#88a
    style ENQ fill:#eef,stroke:#88a
    style APR fill:#eef,stroke:#88a
    style FUP1 fill:#eef,stroke:#88a
    style FUP2 fill:#eef,stroke:#88a
```

Blue-tinted nodes are CRM-owned; everything else is ERP-owned. The two crossing points (`APPT`→`REG` and `SUMMARY`→`FUP2`) are exactly the two domain events `05-integration-architecture.md` formalizes — every other transition is internal to one domain.

## OPD sub-workflow detail

```mermaid
sequenceDiagram
    participant FD as Front Desk
    participant OPD as opd.Encounter
    participant DOC as Doctor
    participant LAB as laboratory
    participant BILL as billing

    FD->>OPD: Check in (from Appointment or walk-in)
    OPD->>OPD: status=waiting, token assigned
    DOC->>OPD: Start consultation (status=in_consultation)
    DOC->>OPD: Record vitals, chief complaints, diagnosis
    opt Investigation needed
        DOC->>LAB: Create InvestigationOrder
        LAB-->>DOC: LabResult (verified)
    end
    DOC->>OPD: Write Prescription, finalize ClinicalNote
    OPD->>OPD: status=completed
    OPD->>BILL: emit encounter_completed event
    BILL->>BILL: Generate OPD bill line items
```

## IPD/discharge sub-workflow detail

```mermaid
sequenceDiagram
    participant DOC as Doctor
    participant ADM as ipd.Admission
    participant NUR as nursing
    participant PH as pharmacy
    participant BILL as billing
    participant CRM as CRM (feedback/automation)

    DOC->>ADM: Admission decision + bed allocation
    loop Daily care
        NUR->>ADM: Vitals, nursing notes, medication administration
        DOC->>ADM: Progress notes
        PH->>ADM: Dispense against prescription
    end
    DOC->>ADM: Discharge decision
    DOC->>ADM: DischargeSummary drafted
    ADM->>BILL: emit discharge_initiated event (draft final bill)
    BILL-->>ADM: Bill finalized, payment/insurance cleared
    DOC->>ADM: DischargeSummary finalized (locked, see 07-audit-and-security.md)
    ADM->>ADM: status=discharged, Bed.status=available
    ADM->>CRM: emit patient_discharged event
    CRM->>CRM: Schedule NPS survey + post-discharge recall task
```
