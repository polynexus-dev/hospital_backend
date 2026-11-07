"""
Dynamic Master Client Legal Agreement Suite & Contract Generator for SaaS Platform Owner.
Generates law-firm-grade, printable, DPDP Act 2023 / Indian law compliant contracts
pre-populated with live tenant data, subscription parameters, module entitlements, and commercials.
"""

from decimal import Decimal
from django.utils import timezone
from apps.core.models import Hospital, CRM_MODULES, HMS_MODULES, ALL_MODULES
from apps.patients.models import Patient
from apps.billing.models import Bill

CRM_MODULE_METADATA = [
    {"key": "telephony", "name": "Telephony & Callbacks", "icon": "📞", "category": "Front Office", "desc": "Inbound call screen-pop, HoduPBX WebRTC phone, missed-call chase queue."},
    {"key": "enquiries", "name": "Enquiries & Leads", "icon": "📥", "category": "Front Office", "desc": "Visual drag-and-drop pipeline, SLA timers, duplicate detection, conversion tracking."},
    {"key": "inbox", "name": "Omnichannel Inbox & WhatsApp", "icon": "💬", "category": "Engagement", "desc": "Unified patient conversation threads, AWS WhatsApp Business API, broadcast campaigns."},
    {"key": "referrals", "name": "Referral Doctor CRM", "icon": "🤝", "category": "Growth", "desc": "External doctor directory, commission ledger, field-officer tracking, revenue attribution."},
    {"key": "packages", "name": "Health Packages & Camps", "icon": "🎁", "category": "Growth", "desc": "Priced diagnostic packages, community health camps, registration-to-admission funnels."},
    {"key": "tpa", "name": "TPA & Insurance Desk", "icon": "🧾", "category": "Front Office", "desc": "Empanelled TPA directory, pre-authorization checklist, claim TAT monitoring."},
    {"key": "feedback", "name": "Feedback & NPS Recovery", "icon": "⭐", "category": "Engagement", "desc": "Automated post-discharge NPS surveys, Google review routing, detractor recovery tasks."},
    {"key": "workflows", "name": "Automation & Recalls", "icon": "⚙️", "category": "Automation", "desc": "Trigger-Condition-Action automation builder, clinical recall workflows, escalation rules."},
]

HMS_MODULE_METADATA = [
    {"key": "opd", "name": "Outpatient Department", "icon": "🩺", "category": "Clinical", "desc": "Clinical consultations, ICD-10 e-Prescriptions, vital checks, instant letterhead PDFs."},
    {"key": "ipd", "name": "Inpatient Department", "icon": "🛏️", "category": "Clinical", "desc": "Admission workflow, live bed occupancy matrix, doctor rounds, discharge summaries."},
    {"key": "nursing", "name": "Nursing Station", "icon": "👩‍⚕️", "category": "Clinical", "desc": "Periodic vitals tracking, Medication Administration Records (MAR), nursing handover."},
    {"key": "laboratory", "name": "Pathology Laboratory", "icon": "🔬", "category": "Diagnostics", "desc": "LIS test catalog, barcoded sample intake, analyzer integration, doctor sign-off."},
    {"key": "radiology", "name": "Radiology & Imaging", "icon": "🩻", "category": "Diagnostics", "desc": "X-Ray, CT, MRI, Ultrasound orders, integrated web DICOM viewer, PACS reporting."},
    {"key": "pharmacy", "name": "Pharmacy POS & Dispensing", "icon": "💊", "category": "Pharmacy", "desc": "e-Rx fulfillment, POS cashier, batch expiry monitoring, drug interaction alerts."},
    {"key": "emergency", "name": "Emergency & Casualty", "icon": "🚨", "category": "Critical Care", "desc": "Red/Yellow/Green trauma triage, medico-legal case (MLC) registry, rapid intake."},
    {"key": "ot", "name": "Operation Theatre", "icon": "🔪", "category": "Critical Care", "desc": "Surgical suite scheduling, surgeon rosters, pre-op checklists, anesthesia safety logs."},
    {"key": "icu", "name": "Intensive Care Unit", "icon": "❤️‍🩹", "category": "Critical Care", "desc": "ICU flowsheets, ventilator settings, central monitor vitals, Apache II / SOFA scores."},
    {"key": "bloodbank", "name": "Blood Bank Management", "icon": "🩸", "category": "Diagnostics", "desc": "Donor registry, component separation (PRBC/FFP/Platelets), crossmatch verification."},
    {"key": "billing", "name": "Patient Billing & Cashier", "icon": "🧾", "category": "Financial", "desc": "Itemized interim/discharge bills, multi-tender payments, GST tax invoice generation."},
    {"key": "inventory", "name": "Hospital Central Inventory", "icon": "📦", "category": "Administrative", "desc": "Department requisitions, purchase orders, vendor invoices, minimum stock reorders."},
    {"key": "finance", "name": "Finance & General Ledger", "icon": "📊", "category": "Financial", "desc": "Double-entry accounting, debit/credit vouchers, doctor payouts, departmental P&L."},
    {"key": "hr", "name": "HR & Staff Shifts", "icon": "👥", "category": "Administrative", "desc": "Doctor duty rosters, nurse shift assignment, biometric attendance, credential vaults."},
    {"key": "telemedicine", "name": "Telemedicine", "icon": "📹", "category": "Clinical", "desc": "Encrypted WebRTC video consults, patient join links, digital consent, e-Prescriptions."},
    {"key": "queue", "name": "Queue & TV Token Display", "icon": "🎫", "category": "Clinical", "desc": "Multi-counter token calling, waiting-room TV public board, multilingual voice prompts."},
    {"key": "portal", "name": "Patient Portal", "icon": "📱", "category": "Patient Engagement", "desc": "Mobile OTP login, appointment booking, lab report downloads, bill payment."},
    {"key": "oncology", "name": "Oncology Specialty", "icon": "🎗️", "category": "Specialty", "desc": "Tumor board notes, chemotherapy cycle staging, radiation therapy charting."},
    {"key": "cathlab", "name": "Cardiac Cath Lab", "icon": "🫀", "category": "Specialty", "desc": "PCI procedure logs, door-to-balloon tracking, stent serial number traceability."},
    {"key": "dietary", "name": "Dietary & Nutrition", "icon": "🍲", "category": "Clinical", "desc": "Doctor therapeutic diet orders (diabetic/renal/bland), kitchen meal tray scheduling."},
    {"key": "infection_control", "name": "Infection Control", "icon": "🧼", "category": "Quality & Compliance", "desc": "HAI surveillance (CLABSI/CAUTI/SSI), antimicrobial stewardship, needle-stick audits."},
    {"key": "quality", "name": "Quality & NABH KPIs", "icon": "🏅", "category": "Quality & Compliance", "desc": "Clinical incident reporting, Sentinel event root-cause, 32 mandatory NABH quality indicators."},
    {"key": "mrd", "name": "Medical Records MRD", "icon": "🗂️", "category": "Quality & Compliance", "desc": "Physical record file tracking, ICD-10 / CPT coding, statutory medico-legal retention."},
    {"key": "schemes", "name": "Government Schemes", "icon": "🏛️", "category": "Financial", "desc": "Ayushman Bharat (PM-JAY), CGHS, ECHS, state health package pre-auth and claim tracking."},
    {"key": "support_services", "name": "Support Services", "icon": "🚑", "category": "Administrative", "desc": "Ambulance dispatch, CSSD sterilization cycles, bio-medical waste, housekeeping logs."},
    {"key": "predictive", "name": "Predictive Analytics", "icon": "📈", "category": "Administrative", "desc": "AI-assisted OPD footfall forecasts, inpatient bed census planning, appointment no-show risk."},
    {"key": "abdm", "name": "ABDM / ABHA Interoperability", "icon": "🆔", "category": "Patient Engagement", "desc": "National Health Authority M1/M2/M3 compliance: ABHA creation, linking, and FHIR HIECM sharing."},
]


def _format_inr(val) -> str:
    try:
        d = Decimal(str(val))
        return f"{d:,.2f}"
    except Exception:
        return str(val)


def generate_hospital_agreement_html(hospital: Hospital, current_user=None) -> str:
    """
    Renders a fully populated, law-firm-grade Master Legal Agreement HTML document
    tailored specifically for the requested Hospital tenant.
    """
    now = timezone.now()
    execution_date_str = now.strftime("%d %B %Y")
    ref_slug = hospital.slug.replace("-", "").upper()[:8]
    agreement_ref = f"PLX-MSA-{now.year}-{ref_slug}"

    # Subscription & Commercial parameters
    subscription = getattr(hospital, "subscription", None)
    tier_name = subscription.get_tier_display() if subscription else "Pro Enterprise"
    billing_cycle = subscription.get_billing_cycle_display() if subscription else "Annual"
    base_price = Decimal(str(subscription.base_price)) if subscription and subscription.base_price else Decimal("132000.00")
    
    # User seat allowance
    max_staff = subscription.max_staff_users if subscription else 0
    seat_label = f"{max_staff} Named Staff Accounts" if max_staff > 0 else "Unlimited Staff Accounts Included"
    
    # Inpatient bed estimation (e.g. from facilities or default)
    authorized_beds = 100
    try:
        from apps.facilities.models import Bed
        bed_count = Bed.objects.filter(hospital=hospital, is_active=True).count()
        if bed_count > 0:
            authorized_beds = bed_count
    except Exception:
        pass

    # Financial calculations
    if subscription and subscription.billing_cycle == "monthly":
        annual_subscription_val = base_price * Decimal("12")
        cadence_desc = f"₹{_format_inr(base_price)} / month (Annualized)"
    else:
        annual_subscription_val = base_price
        cadence_desc = "Annual in Advance"

    implementation_fee = Decimal("85000.00")
    discount_val = Decimal("0.00")
    net_taxable = annual_subscription_val + implementation_fee - discount_val
    gst_val = (net_taxable * Decimal("0.18")).quantize(Decimal("0.01"))
    total_payable = net_taxable + gst_val
    recurring_annual_val = annual_subscription_val + (annual_subscription_val * Decimal("0.18")).quantize(Decimal("0.01"))

    # Enabled modules list
    enabled_keys = set(hospital.enabled_modules or ALL_MODULES)

    # Address & Contact formatting
    address_parts = [hospital.address, hospital.city, hospital.state]
    hospital_address = ", ".join([p.strip() for p in address_parts if p and p.strip()]) or f"{hospital.city or 'City'}, {hospital.state or 'State'}, India"
    
    # Grievance officer
    grievance_name = hospital.grievance_officer_name or "Medical Superintendent / Administrator"
    grievance_email = hospital.grievance_officer_email or f"grievance@{hospital.slug}.hms.polynexus.in"

    # Deployment checkboxes
    is_on_prem = bool(hospital.is_on_premise)
    cloud_checked = "" if is_on_prem else "checked"
    onprem_checked = "checked" if is_on_prem else ""

    # Generate Module Cards HTML
    def render_module_cards(modules_list):
        cards = []
        for mod in modules_list:
            is_checked = mod["key"] in enabled_keys
            checked_attr = "checked" if is_checked else ""
            selected_class = "selected" if is_checked else ""
            cards.append(f"""
            <div class="module-card {selected_class}">
              <input type="checkbox" {checked_attr}>
              <div>
                <div class="mod-title">{mod["icon"]} {mod["name"]} (<code>{mod["key"]}</code>)</div>
                <div class="mod-desc">{mod["desc"]}</div>
              </div>
            </div>""")
        return "\n".join(cards)

    crm_cards_html = render_module_cards(CRM_MODULE_METADATA)
    hms_cards_html = render_module_cards(HMS_MODULE_METADATA)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polynexus Hospital CRM — Master Legal Agreement ({hospital.name})</title>
<style>
  :root {{
    --primary: #0f766e;
    --primary-dark: #115e59;
    --primary-light: #ccfbf1;
    --secondary: #1e293b;
    --text-main: #0f172a;
    --text-muted: #475569;
    --text-light: #64748b;
    --border: #cbd5e1;
    --border-light: #e2e8f0;
    --bg-page: #f8fafc;
    --bg-card: #ffffff;
    --accent: #2563eb;
    --danger: #b91c1c;
    --warning: #d97706;
    --success: #15803d;
  }}

  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: var(--text-main);
    background: #f1f5f9;
    font-size: 13.5px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }}

  /* Sticky Top Action Toolbar */
  .top-toolbar {{
    position: sticky;
    top: 0;
    z-index: 9999;
    background: #0f172a;
    color: #f8fafc;
    padding: 10px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    border-bottom: 1px solid #334155;
  }}

  .top-toolbar .brand-group {{
    display: flex;
    align-items: center;
    gap: 12px;
  }}

  .top-toolbar .logo-badge {{
    background: linear-gradient(135deg, #0d9488, #0f766e);
    color: #ffffff;
    padding: 4px 10px;
    border-radius: 6px;
    font-weight: 800;
    font-size: 13px;
    letter-spacing: 0.05em;
  }}

  .top-toolbar .doc-title {{
    font-size: 13px;
    font-weight: 600;
    color: #e2e8f0;
  }}

  .top-toolbar .nav-links {{
    display: flex;
    align-items: center;
    gap: 16px;
    font-size: 12px;
  }}

  .top-toolbar .nav-links a {{
    color: #94a3b8;
    text-decoration: none;
    transition: color 0.15s;
    font-weight: 500;
  }}

  .top-toolbar .nav-links a:hover {{
    color: #38bdf8;
  }}

  .top-toolbar .actions {{
    display: flex;
    align-items: center;
    gap: 12px;
  }}

  .btn {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 7px 15px;
    border-radius: 6px;
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
    border: none;
    transition: all 0.15s;
    text-decoration: none;
  }}

  .btn-print {{
    background: #0d9488;
    color: #ffffff;
  }}

  .btn-print:hover {{
    background: #0f766e;
    box-shadow: 0 2px 8px rgba(13, 148, 136, 0.4);
  }}

  .edit-notice {{
    font-size: 11px;
    background: #1e293b;
    color: #38bdf8;
    padding: 3px 8px;
    border-radius: 4px;
    border: 1px solid #0284c7;
  }}

  /* Document Container */
  .agreement-container {{
    max-width: 1020px;
    margin: 28px auto 60px auto;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.06);
    padding: 48px 56px;
  }}

  /* Typography */
  h1, h2, h3, h4 {{
    color: var(--secondary);
    font-weight: 700;
    line-height: 1.3;
  }}

  h1 {{
    font-size: 24px;
    margin-bottom: 6px;
  }}

  h2 {{
    font-size: 18px;
    border-bottom: 2px solid var(--primary);
    padding-bottom: 6px;
    margin: 36px 0 16px 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}

  h2 .section-tag {{
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    background: var(--primary-light);
    color: var(--primary-dark);
    padding: 2px 8px;
    border-radius: 4px;
    letter-spacing: 0.05em;
  }}

  h3 {{
    font-size: 14.5px;
    margin: 18px 0 8px 0;
    color: #1e293b;
  }}

  p {{
    margin-bottom: 12px;
    color: var(--text-main);
    text-align: justify;
  }}

  /* Document Header */
  .doc-header {{
    border-bottom: 3px double var(--border);
    padding-bottom: 20px;
    margin-bottom: 28px;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
  }}

  .doc-header .org-badge {{
    display: inline-block;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--primary);
    margin-bottom: 4px;
  }}

  .doc-header .doc-meta {{
    text-align: right;
    font-size: 12px;
    color: var(--text-muted);
  }}

  .doc-header .doc-meta strong {{
    color: var(--secondary);
  }}

  .ref-pill {{
    display: inline-block;
    background: #f1f5f9;
    border: 1px solid #cbd5e1;
    padding: 3px 8px;
    border-radius: 4px;
    font-family: monospace;
    font-weight: 600;
    margin-top: 4px;
    color: #0f172a;
  }}

  /* Tables */
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 14px 0 20px 0;
    font-size: 12.5px;
  }}

  th, td {{
    border: 1px solid var(--border);
    padding: 8px 12px;
    vertical-align: top;
  }}

  th {{
    background: #f8fafc;
    font-weight: 600;
    text-align: left;
    color: var(--secondary);
  }}

  .table-compact td, .table-compact th {{
    padding: 6px 10px;
  }}

  .bg-subtle {{
    background: #f8fafc;
  }}

  /* Modules Grid */
  .modules-grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
    margin: 12px 0 20px 0;
  }}

  .module-card {{
    border: 1px solid var(--border-light);
    border-radius: 6px;
    padding: 8px 10px;
    display: flex;
    align-items: flex-start;
    gap: 8px;
    background: #ffffff;
    font-size: 12px;
  }}

  .module-card.selected {{
    border-color: #0d9488;
    background: #f0fdfa;
  }}

  .module-card input[type="checkbox"] {{
    margin-top: 2px;
    accent-color: #0d9488;
  }}

  .module-card .mod-title {{
    font-weight: 600;
    color: var(--secondary);
  }}

  .module-card .mod-desc {{
    font-size: 11px;
    color: var(--text-light);
    margin-top: 2px;
  }}

  .suite-heading {{
    grid-column: span 2;
    background: #f1f5f9;
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 11.5px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #334155;
    margin-top: 8px;
  }}

  /* Callout Boxes */
  .callout {{
    border-left: 4px solid var(--primary);
    background: #f0fdfa;
    padding: 12px 16px;
    border-radius: 0 6px 6px 0;
    margin: 16px 0;
    font-size: 12.5px;
  }}

  /* Editable Highlight */
  [contenteditable="true"] {{
    outline: none;
    border-bottom: 1px dashed #0284c7;
    background-color: rgba(224, 242, 254, 0.4);
    padding: 0 2px;
    transition: background-color 0.2s;
  }}

  [contenteditable="true"]:hover, [contenteditable="true"]:focus {{
    background-color: rgba(186, 230, 253, 0.8);
    border-bottom: 1px solid #0284c7;
  }}

  /* Execution / Signature Cards */
  .signature-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    margin-top: 24px;
  }}

  .sig-card {{
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 18px;
    background: #fafafa;
  }}

  .sig-card-header {{
    font-weight: 700;
    color: var(--secondary);
    font-size: 13.5px;
    border-bottom: 1px solid var(--border-light);
    padding-bottom: 8px;
    margin-bottom: 14px;
    display: flex;
    justify-content: space-between;
  }}

  .sig-line {{
    margin-top: 36px;
    border-bottom: 1px solid #64748b;
    padding-bottom: 4px;
    font-size: 11px;
    color: var(--text-light);
  }}

  .sig-field {{
    margin-top: 8px;
    font-size: 12px;
  }}

  /* Footer */
  .doc-footer {{
    border-top: 1px solid var(--border-light);
    padding-top: 14px;
    margin-top: 40px;
    font-size: 11px;
    color: var(--text-light);
    display: flex;
    justify-content: space-between;
  }}

  /* Print Styles */
  @media print {{
    .no-print {{
      display: none !important;
    }}

    body {{
      background: #ffffff !important;
      color: #000000 !important;
      font-size: 10.5pt;
      line-height: 1.45;
    }}

    .agreement-container {{
      max-width: 100% !important;
      margin: 0 !important;
      padding: 0 !important;
      border: none !important;
      box-shadow: none !important;
    }}

    .page-break {{
      page-break-before: always;
      break-before: page;
      margin-top: 20mm;
    }}

    [contenteditable="true"] {{
      border-bottom: none !important;
      background-color: transparent !important;
    }}

    table {{
      page-break-inside: auto;
    }}

    tr {{
      page-break-inside: avoid;
      page-break-after: auto;
    }}

    @page {{
      size: A4;
      margin: 18mm 15mm 20mm 15mm;
    }}
  }}
</style>
</head>
<body>

<!-- Sticky Dark Toolbar -->
<div class="top-toolbar no-print">
  <div class="brand-group">
    <span class="logo-badge">POLYNEXUS</span>
    <span class="doc-title">Master Client Legal Agreement Suite (MSA · On-Prem · DPA · SLA · EULA)</span>
  </div>
  <div class="nav-links">
    <a href="#order-form">Order Form</a>
    <a href="#part-1">Part I: MSA</a>
    <a href="#part-2">Part II: On-Prem</a>
    <a href="#part-3">Part III: DPA</a>
    <a href="#part-4">Part IV: SLA</a>
    <a href="#part-5">Part V: EULA</a>
    <a href="#execution">Signatures</a>
  </div>
  <div class="actions">
    <span class="edit-notice">✏️ Live Contract (Click any text to adjust)</span>
    <button class="btn btn-print" onclick="window.print()">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
      Print / Save as PDF
    </button>
  </div>
</div>

<!-- Main Printable Agreement Container -->
<div class="agreement-container">

  <!-- Document Cover / Header -->
  <div class="doc-header">
    <div>
      <span class="org-badge">Confidential · B2B Enterprise Agreement</span>
      <h1>Master Client Legal Agreement Suite</h1>
      <p style="color: var(--text-muted); font-size: 13px; margin-top: 2px;">
        Governing the SaaS Subscription, On-Premise Software Licensing, Data Processing (DPDP Act 2023), and Managed Support of <strong>Polynexus Hospital CRM & Healthcare ERP Platform</strong>
      </p>
    </div>
    <div class="doc-meta">
      <div>Agreement Reference:</div>
      <div class="ref-pill" contenteditable="true">{agreement_ref}</div>
      <div style="margin-top: 6px;">Execution Date: <strong contenteditable="true">{execution_date_str}</strong></div>
      <div>Jurisdiction: <strong>Nagpur / Maharashtra, India</strong></div>
    </div>
  </div>

  <!-- SECTION 1: COMMERCIAL ORDER FORM & CLIENT SCHEDULE -->
  <section id="order-form">
    <h2>
      <span>Commercial Order Form & Client Schedule</span>
      <span class="section-tag">Schedule A</span>
    </h2>
    <p>
      This Commercial Order Form & Client Schedule is executed under and subject to the Master Terms set forth in Parts I through V herein. By executing below, the Client agrees to license and subscribe to the products, modules, and services specified below.
    </p>

    <!-- KYC & Entity Mapping Table -->
    <table>
      <thead>
        <tr>
          <th style="width: 50%;">1. PROVIDER DETAILS (LICENSOR / PROCESSOR)</th>
          <th style="width: 50%;">2. CLIENT DETAILS (LICENSEE / DATA FIDUCIARY)</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>
            <strong>Legal Name:</strong> Polynexus Technologies Private Limited<br>
            <strong>Corporate Identity (CIN):</strong> U72900MH2024PTC428912<br>
            <strong>PAN:</strong> AAECP1234F &nbsp;|&nbsp; <strong>GSTIN:</strong> 27AAECP1234F1Z5<br>
            <strong>Registered Office:</strong> Office No. 402, Supreme IT Park, Tech Zone, Nagpur, Maharashtra – 440022, India<br>
            <strong>Corporate Email:</strong> admin@polynexus.in / legal@polynexus.in<br>
            <strong>Authorized Signatory:</strong> Managing Director & CEO<br>
            <strong>Grievance & Data Officer:</strong> privacy@polynexus.in
          </td>
          <td>
            <strong>Legal Entity Name:</strong> <span contenteditable="true">{hospital.name}</span><br>
            <strong>Clinical Reg. / CIN:</strong> <span contenteditable="true">REG-{hospital.slug.upper()[:6]}-2026</span><br>
            <strong>PAN:</strong> <span contenteditable="true">AAACS{hospital.slug.upper()[:4]}K</span> &nbsp;|&nbsp; <strong>GSTIN:</strong> <span contenteditable="true">27AAACS{hospital.slug.upper()[:4]}K1ZQ</span><br>
            <strong>Registered Hospital Address:</strong> <span contenteditable="true">{hospital_address}</span><br>
            <strong>Designated Contact Person:</strong> <span contenteditable="true">Medical Superintendent / Managing Trustee</span><br>
            <strong>Official Email:</strong> <span contenteditable="true">admin@{hospital.slug}.hms.polynexus.in</span><br>
            <strong>Grievance Officer:</strong> <span contenteditable="true">{grievance_name} ({grievance_email})</span>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- Subscription & Tenancy Architecture -->
    <table>
      <thead>
        <tr>
          <th colspan="2">3. SUBSCRIPTION PARAMETERS & DEPLOYMENT ARCHITECTURE</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td style="width: 50%;">
            <strong>Deployment Model:</strong><br>
            <label><input type="checkbox" {cloud_checked}> Multi-Tenant Cloud SaaS (Shared High-Availability Infrastructure)</label><br>
            <label><input type="checkbox"> Dedicated Private Cloud (Single-Tenant Virtual Private Cloud)</label><br>
            <label><input type="checkbox" {onprem_checked}> On-Premise / Self-Hosted (Local Docker Cluster / Hardware Server)</label>
          </td>
          <td style="width: 50%;">
            <strong>Assigned Production Subdomain:</strong><br>
            <code style="font-size: 13px; color: #0d9488; font-weight: bold;">https://<span contenteditable="true">{hospital.slug}</span>.hms.polynexus.in</code><br>
            <span style="font-size: 11.5px; color: var(--text-light);">Primary Region: AWS Mumbai (ap-south-1) · Data Residency: India</span>
          </td>
        </tr>
        <tr>
          <td>
            <strong>Subscription Plan Tier:</strong> <span contenteditable="true">{tier_name}</span><br>
            <strong>Billing Frequency:</strong> <span contenteditable="true">{billing_cycle}</span><br>
            <strong>Initial Term:</strong> <span contenteditable="true">12 Months (Commencing {execution_date_str})</span>
          </td>
          <td>
            <strong>Active Staff User License Cap:</strong> <span contenteditable="true">{seat_label}</span><br>
            <strong>Authorized Inpatient Bed Capacity:</strong> <span contenteditable="true">{authorized_beds} Operational Beds</span><br>
            <strong>Trial / Evaluation Window:</strong> <span contenteditable="true">Waived / Direct Production Rollout</span>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- Module Catalog Grid -->
    <h3>4. Licensed Feature & Module Entitlement Grid</h3>
    <p style="font-size: 12px; color: var(--text-muted); margin-bottom: 8px;">
      Checked modules indicate active entitlement under this Agreement. Access is strictly managed via server-enforced Tenant Module Middlewares.
    </p>

    <div class="modules-grid">
      <!-- Shared Baseline -->
      <div class="suite-heading">Shared Core Baseline (Always Included)</div>
      <div class="module-card selected">
        <input type="checkbox" checked disabled>
        <div>
          <div class="mod-title">👤 Patient 360 & Timeline Vault</div>
          <div class="mod-desc">Unified demographic profile, lifetime visit timeline, encrypted document repository.</div>
        </div>
      </div>
      <div class="module-card selected">
        <input type="checkbox" checked disabled>
        <div>
          <div class="mod-title">📅 Clash-Free OPD Appointment Scheduler</div>
          <div class="mod-desc">Doctor slot management, multi-channel booking, SMS/WhatsApp reminders.</div>
        </div>
      </div>

      <!-- CRM Suite -->
      <div class="suite-heading">CRM & Patient Acquisition Suite (8 Modules)</div>
      {crm_cards_html}

      <!-- HMS Core Suite -->
      <div class="suite-heading">HMS Clinical & Operations Suite (27 Modules)</div>
      {hms_cards_html}
    </div>

    <!-- Commercial Pricing Schedule -->
    <h3>5. Commercial Pricing & Payment Terms</h3>
    <table class="table-compact">
      <thead>
        <tr>
          <th>LINE ITEM DESCRIPTION</th>
          <th>BILLING CADENCE</th>
          <th style="text-align: right;">AMOUNT (INR)</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>
            <strong>Polynexus Hospital Platform Subscription Fee ({tier_name})</strong><br>
            <span style="font-size: 11.5px; color: var(--text-light);">Includes all selected modules, unlimited staff logins, daily automated encrypted backups, regular security patches and cloud hosting.</span>
          </td>
          <td>{cadence_desc}</td>
          <td style="text-align: right; font-weight: 600;">₹<span contenteditable="true">{_format_inr(annual_subscription_val)}</span></td>
        </tr>
        <tr>
          <td>
            <strong>One-Time Implementation, Setup & Staff Onboarding</strong><br>
            <span style="font-size: 11.5px; color: var(--text-light);">Dedicated onboarding engineer, master data import (doctors, tariffs, beds), custom letterheads, 3 weeks hypercare support.</span>
          </td>
          <td>One-Time (Year 1 only)</td>
          <td style="text-align: right; font-weight: 600;">₹<span contenteditable="true">{_format_inr(implementation_fee)}</span></td>
        </tr>
        <tr>
          <td>
            <strong>Promotional / Multi-Module Commercial Discount</strong>
          </td>
          <td>Special Credit</td>
          <td style="text-align: right; color: var(--success); font-weight: 600;">- ₹<span contenteditable="true">{_format_inr(discount_val)}</span></td>
        </tr>
        <tr class="bg-subtle">
          <td colspan="2"><strong>Taxable Subtotal (Net Amount)</strong></td>
          <td style="text-align: right; font-weight: 700;">₹<span contenteditable="true">{_format_inr(net_taxable)}</span></td>
        </tr>
        <tr>
          <td colspan="2"><strong>Goods & Services Tax (GST) @ 18% (SAC Code: 998313)</strong></td>
          <td style="text-align: right; font-weight: 600;">₹<span contenteditable="true">{_format_inr(gst_val)}</span></td>
        </tr>
        <tr style="background: #f0fdfa; border-top: 2px solid var(--primary);">
          <td colspan="2"><strong style="font-size: 13.5px; color: var(--primary-dark);">TOTAL PAYABLE (YEAR 1 INCLUSIVE OF GST)</strong></td>
          <td style="text-align: right; font-size: 14px; font-weight: 800; color: var(--primary-dark);">₹<span contenteditable="true">{_format_inr(total_payable)}</span></td>
        </tr>
        <tr>
          <td colspan="2"><span style="color: var(--text-muted); font-size: 11.5px;">Annual Recurring Renewal (Year 2 Onward, flat rate with no re-implementation fee)</span></td>
          <td style="text-align: right; font-size: 12px; color: var(--text-muted);">₹<span contenteditable="true">{_format_inr(recurring_annual_val)}</span> / year</td>
        </tr>
      </tbody>
    </table>

    <div class="callout">
      <strong>Payment Gateway Surcharge Election & Telephony Pass-Through:</strong><br>
      • <strong>Payment Gateway:</strong> Selected Option: <strong>Customer Surcharge Pass-Through Mode</strong> (Standard convenience fee levied directly onto payors via Razorpay / Cashfree / PayU). The Provider serves as a software facilitation layer and is not a Merchant-of-Record.<br>
      • <strong>Third-Party Telephony / WhatsApp:</strong> Cloud telephony (IVR/SIP via HoduPBX) and WhatsApp Business API message consumption are billed at transparent actual provider costs with zero platform markup.
    </div>

    <table>
      <thead>
        <tr>
          <th colspan="2">6. DESIGNATED BANK ACCOUNT FOR B2B NEFT / RTGS / IMPS PAYMENTS</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td style="width: 50%;">
            <strong>Account Name:</strong> POLYNEXUS TECHNOLOGIES PRIVATE LIMITED<br>
            <strong>Bank Name:</strong> HDFC Bank Ltd.<br>
            <strong>Account Number:</strong> 50200088991122
          </td>
          <td style="width: 50%;">
            <strong>Account Type:</strong> Current Account<br>
            <strong>IFSC Code:</strong> HDFC0000123<br>
            <strong>Branch:</strong> Tech Zone Branch, Nagpur, Maharashtra – 440022
          </td>
        </tr>
      </tbody>
    </table>
  </section>

  <!-- SECTION 2: PART I — MASTER SAAS SUBSCRIPTION & SERVICES AGREEMENT (MSA) -->
  <section id="part-1" class="page-break">
    <h2>
      <span>Part I — Master SaaS Subscription & Services Agreement (MSA)</span>
      <span class="section-tag">Core MSA</span>
    </h2>

    <p>
      This Master SaaS Subscription and Services Agreement (“<strong>Agreement</strong>”) is made and entered into as of the Effective Date specified in Schedule A by and between <strong>Polynexus Technologies Private Limited</strong> (“<strong>Provider</strong>” or “<strong>Licensor</strong>”), and the healthcare entity identified as Client in Schedule A (“<strong>Client</strong>” or “<strong>Licensee</strong>”). Provider and Client are collectively referred to as the “<strong>Parties</strong>” and individually as a “<strong>Party</strong>”.
    </p>

    <h3>1. License Grant, Scope of Use & Access Rights</h3>
    <p>
      1.1 <strong>Subscription Grant:</strong> Subject to timely payment of the agreed Fees and compliance with this Agreement, Provider grants to Client a non-exclusive, non-transferable, non-sublicensable, revocable right and license during the Subscription Term to access and use the hosted Polynexus Hospital CRM and Healthcare ERP software applications (“<strong>Software</strong>” or “<strong>Platform</strong>”) solely for Client’s internal clinical, administrative, and operational hospital purposes.
    </p>
    <p>
      1.2 <strong>Module-Level Entitlement & RBAC:</strong> Client’s license is strictly limited to the specific modules identified as enabled in Schedule A. Client shall administer end-user access utilizing Role-Based Access Control (RBAC) in accordance with the principle of least privilege. Client administrators shall not circumvent module entitlement ceilings configured in the SaaS Console.
    </p>
    <p>
      1.3 <strong>Evaluation & Sandboxes:</strong> Any sandbox, demonstration, or trial instance made available to Client is provided strictly “AS-IS” without warranty or SLA commitments, solely for evaluation purposes, and may be disabled by Provider at any time without prior notice.
    </p>

    <h3>2. Invoicing, Payments, Taxes & Account Suspension</h3>
    <p>
      2.1 <strong>Invoicing Cadence:</strong> Provider shall issue B2B tax invoices reflecting consecutive, gapless invoice serial numbers per Indian Financial Year (INV-YYYY-YY-XXXXX). Invoices shall be payable within fifteen (15) calendar days from the invoice date.
    </p>
    <p>
      2.2 <strong>Offline NEFT/RTGS Verification:</strong> For payments remitted via NEFT, RTGS, or IMPS, Client shall submit the valid Bank Unique Transaction Reference (UTR) and payment receipt in the SaaS Billing Console. Credits shall be posted upon automated or banking ledger verification.
    </p>
    <p>
      2.3 <strong>Goods and Services Tax (GST):</strong> All fees are exclusive of GST. Client shall remit applicable GST (currently 18% under SAC Code 998313) simultaneously with the base fee. Provider shall issue a valid GST tax invoice enabling Client to claim Input Tax Credit (ITC).
    </p>
    <p>
      2.4 <strong>Tax Deducted at Source (TDS):</strong> Where Client is legally mandated to deduct TDS under Section 194J or 194C of the Income Tax Act, 1961, Client shall remit the net amount and furnish the statutory TDS Certificate (Form 16A) within fifteen (15) days of the statutory quarterly filing due date. Failure to provide Form 16A shall entitle Provider to demand reimbursement of the withheld amount.
    </p>
    <p>
      2.5 <strong>Late Interest & Graceful Suspension:</strong> Unpaid amounts past due date shall attract commercial interest at the rate of 1.5% per month (18% per annum) calculated daily. If any invoice remains overdue for more than twenty-one (21) calendar days following written notice, Provider reserves the right to suspend Client’s access to the Software until all outstanding dues are cleared.
    </p>

    <h3>3. Third-Party Integrations & Merchant-of-Record Disclaimer</h3>
    <p>
      3.1 <strong>Payment Gateway Facilitation:</strong> The Software facilitates integration with third-party payment aggregators (e.g., Razorpay, Cashfree, PayU, PhonePe) to collect patient payments. Client acknowledges that Provider is solely a technical integration provider and is <strong>not a Merchant of Record, payment aggregator, or financial intermediary</strong> under the Payment and Settlement Systems Act, 2007. Client must maintain its own direct merchant account and complies with all Reserve Bank of India (RBI) KYC guidelines.
    </p>
    <p>
      3.2 <strong>Telephony and Messaging Pass-Through:</strong> Cloud telephony services (HoduPBX, SIP trunks) and WhatsApp Business API messaging are provisioned via licensed telecom carriers and Meta Business Solution Providers. Charges incurred are billed at actuals. Provider bears no liability for network outages or regulatory carrier delivery delays.
    </p>

    <h3>4. Intellectual Property Rights & Prohibited Uses</h3>
    <p>
      4.1 <strong>Provider IP:</strong> Provider retains all right, title, and interest, including all patent, copyright, trademark, trade secret, and other intellectual property rights, in and to the Software, source code, database architectures, machine learning models, algorithms, workflows, documentation, and all enhancements or modifications thereto.
    </p>
    <p>
      4.2 <strong>Client Ownership of Patient Data:</strong> As between Provider and Client, Client owns all right, title, and interest in and to all clinical data, Electronic Health Records (EHR), patient demographics, and transactional financial records uploaded or ingested into the Platform (“<strong>Client Data</strong>”).
    </p>
    <p>
      4.3 <strong>Restrictions:</strong> Client shall not, directly or indirectly: (a) reverse engineer, decompile, disassemble, or attempt to derive the source code of the Software; (b) publish, license, rent, lease, or resell the Platform; (c) build a competitive commercial product or service; or (d) publish benchmarks or vulnerability scan reports without Provider’s prior written consent.
    </p>

    <h3>5. Confidentiality & Non-Disclosure</h3>
    <p>
      Each Party agrees to protect the Confidential Information of the other Party with the same degree of care it uses for its own confidential materials (and not less than reasonable care). Confidential Information includes software source code, technical specifications, commercial pricing, security reports, and all Protected Health Information (PHI). This obligation survives termination of this Agreement for five (5) years, provided trade secrets and PHI shall remain confidential indefinitely.
    </p>

    <h3>6. Clinical Disclaimer & Allocation of Medical Responsibility</h3>
    <p>
      <strong>THE SOFTWARE IS AN ADMINISTRATIVE, WORKFLOW, AND CLINICAL DOCUMENTATION SUPPORT TOOL ONLY. THE SOFTWARE DOES NOT PRACTICE MEDICINE OR PROVIDE MEDICAL ADVICE.</strong> All clinical diagnosis, prescription writing, drug allergy verification, surgical decisions, emergency triage categorizations, and patient care management remain the exclusive, non-delegable professional responsibility of Client’s licensed medical practitioners. Provider disclaims all liability arising from clinical errors, medical negligence, or misdiagnosis by Client’s staff.
    </p>

    <h3>7. Mutual Indemnification</h3>
    <p>
      7.1 <strong>Provider IP Indemnity:</strong> Provider shall defend and indemnify Client against any third-party claim alleging that Client’s authorized use of the Software infringes any Indian patent or copyright, provided Client promptly notifies Provider in writing, grants sole defense control, and provides reasonable cooperation.
    </p>
    <p>
      7.2 <strong>Client Indemnity:</strong> Client shall defend and indemnify Provider and its directors, officers, and employees against any claims, losses, or penalties arising from: (a) Client Data infringing privacy rights or violating the DPDP Act 2023; (b) clinical malpractice or medical negligence claims by patients; or (c) unauthorized access resulting from compromised Client staff credentials.
    </p>

    <h3>8. Limitation of Liability</h3>
    <p>
      8.1 <strong>Aggregate Cap:</strong> TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT SHALL EITHER PARTY’S TOTAL AGGREGATE LIABILITY ARISING OUT OF OR RELATED TO THIS AGREEMENT, WHETHER IN CONTRACT, TORT (INCLUDING NEGLIGENCE), OR OTHERWISE, EXCEED THE TOTAL FEES ACTUALLY PAID BY CLIENT TO PROVIDER IN THE TWELVE (12) MONTHS PRECEDING THE EVENT GIVING RISE TO LIABILITY.
    </p>
    <p>
      8.2 <strong>Consequential Damages Waiver:</strong> NEITHER PARTY SHALL BE LIABLE FOR ANY INDIRECT, INCIDENTAL, CONSEQUENTIAL, SPECIAL, PUNITIVE, OR EXEMPLARY DAMAGES, INCLUDING LOSS OF REVENUE, LOSS OF PROFITS, BUSINESS INTERRUPTION, LOSS OF REPUTATION, OR LOSS OF DATA, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.
    </p>

    <h3>9. Term, Termination & 30-Day Data Transition</h3>
    <p>
      9.1 <strong>Termination for Cause:</strong> Either Party may terminate this Agreement upon thirty (30) days’ written notice if the other Party materially breaches any term and fails to cure such breach within the thirty-day cure period.
    </p>
    <p>
      9.2 <strong>30-Day Data Export Window:</strong> Upon termination or expiration of this Agreement, Provider shall maintain Client’s tenant account in read-only data extraction mode for thirty (30) calendar days. Client shall have the unrestricted right to export all Client Data in standard open formats (JSON, CSV, and HL7 FHIR R4 clinical bundles). Upon expiry of thirty (30) days, Provider shall securely cryptographically wipe and purge all tenant data from active databases in accordance with DoD 5220.22-M / NIST 800-88 standards.
    </p>

    <h3>10. Dispute Resolution, Governing Law & Indian Arbitration</h3>
    <p>
      This Agreement shall be governed by and construed in accordance with the laws of the Republic of India. Any dispute, controversy, or claim arising out of or relating to this Agreement shall be referred to and finally resolved by binding arbitration under the <strong>Arbitration and Conciliation Act, 1996</strong> by a sole arbitrator mutually appointed by the Parties (or, failing agreement, appointed by the High Court of Judicature at Bombay). The seat and legal place of arbitration shall be <strong>Nagpur / Pune, Maharashtra</strong>. The proceedings shall be conducted in the English language. Subject to arbitration, the courts situated at Nagpur / Pune shall have exclusive jurisdiction.
    </p>
  </section>

  <!-- SECTION 3: PART II — ON-PREMISE / SELF-HOSTED SOFTWARE LICENSE ADDENDUM -->
  <section id="part-2" class="page-break">
    <h2>
      <span>Part II — On-Premise / Self-Hosted Software License Addendum</span>
      <span class="section-tag">Addendum B</span>
    </h2>

    <p>
      This On-Premise / Self-Hosted Software License Addendum applies whenever Client elects the On-Premise or Dedicated Server deployment model in Schedule A.
    </p>

    <h3>1. Scope of Node-Locked License & License File Issuance</h3>
    <p>
      1.1 <strong>Single-Instance Node License:</strong> Licensor grants to Licensee a non-exclusive, non-transferable license to install and execute the compiled Docker container bundle of the Software strictly on a designated physical or virtual server instance matching the unique <code>Deployment ID</code> and cryptographic hardware SHA-256 machine fingerprint recorded in the SaaS Console.
    </p>
    <p>
      1.2 <strong>Ed25519 Cryptographic License Key:</strong> Software operation is validated by an offline cryptographic license key file (<code>hospital.lic</code>) digitally signed with Licensor’s Ed25519 private key. The license key strictly enforces authorized feature sets, bed capacity limits, user counts, and expiry dates. Licensee shall not tamper with, patch, reverse engineer, or emulate the license verification daemon.
    </p>

    <h3>2. Intellectual Property Protection & Bytecode Sanctity</h3>
    <p>
      2.1 <strong>Strict Binary Delivery:</strong> The Software is delivered exclusively as compiled Python bytecode (<code>.pyc</code> / pyinstaller / Docker images) and bundled frontend assets. Licensor does not deliver, license, or convey underlying human-readable source code.
    </p>
    <p>
      2.2 <strong>Prohibition of Reverse Engineering:</strong> Licensee shall not disassemble, decompile, extract, or inspect the container images or bytecode. Licensee shall not push, commit, or mirror any portion of the software images, schema migrations, or configurations to any public or external Git repository.
    </p>

    <h3>3. Client Infrastructure, Security & CERT-In Responsibilities</h3>
    <p>
      3.1 <strong>Hardware & OS Prerequisites:</strong> Client is solely responsible for procuring, configuring, and maintaining the underlying host servers, hypervisors, PostgreSQL database instances, local area network (LAN) switches, uninterruptible power supply (UPS), and operating system security updates.
    </p>
    <p>
      3.2 <strong>Backups & Disaster Recovery:</strong> In on-premise deployments, <strong>Client is solely responsible for executing daily off-site backups</strong> of the database, media storage, and document vault. Licensor bears no liability for unrecoverable data loss resulting from hardware failure, storage corruption, ransomware attacks, or local disasters.
    </p>
    <p>
      3.3 <strong>CERT-In Compliance:</strong> Client shall maintain host system logs for 180 days and comply with all cybersecurity directives issued by the Indian Computer Emergency Response Team (CERT-In) under Section 70B of the Information Technology Act, 2000.
    </p>

    <h3>4. Annual Maintenance Contract (AMC) Scope & Exclusions</h3>
    <p>
      4.1 <strong>AMC Entitlements:</strong> An active AMC entitles Client to: (a) scheduled Software version upgrades, regulatory updates (e.g., ABDM schema revisions, GST tax rate changes), and critical security patches; and (b) remote technical assistance via secure SSH/VPN tunnels during standard business hours.
    </p>
    <p>
      4.2 <strong>Exclusions:</strong> AMC does not cover: (a) physical hardware repairs; (b) remediation of corrupted databases caused by ungraceful power outages or third-party tampering; (c) third-party operating system or database administrator support; or (d) custom code modifications.
    </p>

    <h3>5. Remote / On-Site Audit Rights</h3>
    <p>
      Licensor reserves the right, upon seven (7) business days’ prior written notice, to conduct an electronic audit or on-site inspection of Licensee’s software instance to verify compliance with active license parameters, user counts, and enabled modules. If an audit reveals unauthorized usage exceeding permitted limits by more than five percent (5%), Licensee shall promptly pay Licensor the applicable underpaid fees plus reasonable audit costs.
    </p>
  </section>

  <!-- SECTION 4: PART III — DATA PROCESSING AGREEMENT (DPA) & PRIVACY COMPLIANCE -->
  <section id="part-3" class="page-break">
    <h2>
      <span>Part III — Data Processing Agreement (DPA) & Privacy Compliance</span>
      <span class="section-tag">DPDP Act 2023 Compliant</span>
    </h2>

    <p>
      This Data Processing Agreement (“<strong>DPA</strong>”) supplements the Master Agreement and governs the processing of personal data and digital health records pursuant to the <strong>Digital Personal Data Protection Act, 2023 (“DPDP Act”)</strong>, the <strong>Information Technology Act, 2000</strong>, and the <strong>Information Technology (Reasonable Security Practices and Procedures and Sensitive Personal Data or Information) Rules, 2011 (“SPDI Rules”)</strong>.
    </p>

    <h3>1. Statutory Characterization & Scope of Processing</h3>
    <p>
      1.1 <strong>Parties’ Roles:</strong> Under the DPDP Act 2023, <strong>Client is the Data Fiduciary</strong> who determines the purpose and means of processing patient and staff personal data. <strong>Provider is the Data Processor</strong> engaged by the Data Fiduciary to process personal data strictly on its behalf and in accordance with its documented technical instructions.
    </p>
    <p>
      1.2 <strong>Instruction Boundaries:</strong> Provider shall process personal data solely as necessary to provide the Software services described in the Master Agreement and shall not process Client Personal Data for any secondary purpose, advertising, or profiling.
    </p>

    <h3>2. Mapping of Data Categories & Lawful Processing Purposes</h3>
    <table>
      <thead>
        <tr>
          <th>DATA PRINCIPAL CATEGORY</th>
          <th>DATA TYPES / FIELDS PROCESSED</th>
          <th>LAWFUL PURPOSE UNDER DPDP ACT</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Outpatients & Inpatients</strong></td>
          <td>Full name, phone, age/DOB, gender, address, UHID, ABHA ID, photo, emergency contacts.</td>
          <td>Patient registration, medical record indexing, identity verification, ABDM linking.</td>
        </tr>
        <tr>
          <td><strong>Clinical & Diagnostic Data</strong></td>
          <td>Doctor clinical notes, ICD-10 diagnoses, lab pathology results, DICOM radiology imaging, vitals.</td>
          <td>Patient healthcare delivery, clinical diagnosis, inpatient monitoring, e-Prescriptions.</td>
        </tr>
        <tr>
          <td><strong>Financial & Payer Records</strong></td>
          <td>Invoices, advance receipts, transaction IDs, insurance policy numbers, TPA claim documents.</td>
          <td>Billing, payment reconciliation, insurance reimbursement, statutory accounting.</td>
        </tr>
        <tr>
          <td><strong>Telephony & Audio</strong></td>
          <td>Inbound/outbound call audio recordings, IVR logs, missed-call records, WhatsApp threads.</td>
          <td>Patient care coordination, call-center quality assurance, medical dispute resolution.</td>
        </tr>
        <tr>
          <td><strong>Hospital Staff & Doctors</strong></td>
          <td>Name, council registration no., digital signatures, official stamps, shift attendance logs.</td>
          <td>Prescription authentication, clinical audit trails, shift management, system access.</td>
        </tr>
      </tbody>
    </table>

    <h3>3. Special Safeguards for Sensitive Categories of Processing</h3>
    <p>
      3.1 <strong>Biometrics & Doctor Digital Signatures:</strong> Where the Platform captures staff biometric hashes or doctor digital signatures/stamps (for signing e-Prescriptions or discharge summaries under NABH COP.1.e), such data is treated as sensitive personal data. Raw biometric templates are never stored; cryptographic hashes and encrypted vectors are stored in isolated vaults accessible only by authorized signature stamping services.
    </p>
    <p>
      3.2 <strong>Device Binding, IP Whitelisting & Devtools Protection:</strong> The Platform implements strict user IP-range whitelisting (CIDR validation), multi-factor authentication (TOTP), and optional browser devtools suppression to prevent unauthorized off-premises session hijacking and inspection of cached clinical records.
    </p>
    <p>
      3.3 <strong>Verifiable Parental / Guardian Consent for Minors (Section 9, DPDP Act 2023):</strong> For any patient under eighteen (18) years of age, the Platform enforces mandatory capture of lawful parent or legal guardian details and consent status (<code>PatientConsent.GivenBy.GUARDIAN</code>). The Platform strictly prohibits behavioral tracking, targeted advertisements, or automated profiling directed at minors.
    </p>

    <h3>4. Data Principal Rights Fulfillment</h3>
    <p>
      4.1 <strong>Assistance to Data Fiduciary:</strong> Provider provides technical modules (<code>apps.privacy</code>) enabling Client to promptly honor Data Principal rights under Chapter III of the DPDP Act 2023, including: (a) right to access processing summary; (b) right to correction and completion; (c) right to erasure (subject to statutory medical retention mandates); (d) right to grievance redressal; and (e) right to nominate an individual under Section 14 in the event of death or incapacity.
    </p>
    <p>
      4.2 <strong>Statutory Medical Record Retention Exception:</strong> Client acknowledges that clinical health records, inpatient charts, and MLC registers are subject to statutory retention guidelines issued by the National Medical Commission (NMC) and NABH (typically 3 to 10 years). Where a patient requests data erasure, the Platform facilitates soft-deletion and de-identification while preserving statutory audit logs required by law.
    </p>

    <h3>5. Technical & Organizational Measures (TOMs)</h3>
    <p>
      Provider warrants and maintains the following security controls:
    </p>
    <ul style="margin-left: 20px; margin-bottom: 12px;">
      <li><strong>Field-Level Encryption at Rest:</strong> Identifiable patient fields (phone numbers, Aadhaar hashes, PAN, clinical notes) are encrypted at rest using AES-256-GCM and Fernet cryptographic primitives with segregated key rotation policies.</li>
      <li><strong>Encryption in Transit:</strong> Mandatory TLS 1.3 encryption across all public and internal microservice HTTP/WebSocket endpoints.</li>
      <li><strong>Layer-2 Application Payload Encryption:</strong> Browser-to-server end-to-end payload encryption (ECDH key exchange) masking JSON payloads in transit from browser inspect tools.</li>
      <li><strong>Immutable Audit Trails:</strong> Append-only, tamper-evident audit logging (<code>AuditLog</code>) recording user actor, timestamp, IP address, request method, and model change diffs.</li>
      <li><strong>Break-Glass Emergency Protocol:</strong> Tracked, encrypted audit logging for emergency break-glass clinical data access (<code>EmergencyAccessLog</code>) reviewed by hospital medical compliance officers.</li>
    </ul>

    <h3>6. 48-Hour Personal Data Breach Notification</h3>
    <p>
      In the event of any confirmed security incident, unauthorized exfiltration, or breach affecting Client Personal Data, Provider shall notify Client’s designated Grievance / Data Protection Officer without undue delay and in any event <strong>within forty-eight (48) hours of confirmation</strong>. Provider shall provide a comprehensive incident report detailing the nature of the breach, affected records, immediate remedial actions taken, and technical guidance to enable Client to fulfill its mandatory breach reporting obligations to the <strong>Data Protection Board of India (DPBI)</strong> and <strong>CERT-In</strong>.
    </p>

    <h3>7. Sub-processors & International Data Transfers</h3>
    <p>
      Provider utilizes pre-vetted cloud infrastructure sub-processors (AWS ap-south-1 Mumbai, Equinix/CtrlS Datacenters, AWS End User Messaging). All personal data of Indian Data Principals is stored strictly on servers located within the territory of India, in full compliance with Indian data sovereignty mandates. Provider shall provide thirty (30) days’ advance notice before engaging any new sub-processor.
    </p>
  </section>

  <!-- SECTION 5: PART IV — SERVICE LEVEL AGREEMENT (SLA) & SUPPORT POLICY -->
  <section id="part-4" class="page-break">
    <h2>
      <span>Part IV — Service Level Agreement (SLA) & Technical Support Policy</span>
      <span class="section-tag">SLA Schedule</span>
    </h2>

    <p>
      This Service Level Agreement (“<strong>SLA</strong>”) sets forth Provider’s uptime commitments, incident response timelines, and support escalation protocols for the SaaS Platform.
    </p>

    <h3>1. Monthly Uptime Commitment & Availability Formula</h3>
    <p>
      Provider commits to maintain an aggregate Monthly Availability Percentage of not less than <strong>99.5%</strong> for the production SaaS Platform during each calendar month.
    </p>
    <p>
      Monthly Uptime Percentage is calculated using the following statutory formula:
    </p>
    <div style="background: #f8fafc; border: 1px solid var(--border); padding: 10px 14px; border-radius: 6px; font-family: monospace; font-size: 12px; margin: 10px 0;">
      Monthly Availability % = [ (Total Operational Minutes − Excused Downtime − Unplanned Downtime) / (Total Operational Minutes − Excused Downtime) ] × 100
    </div>
    <p>
      <strong>Excused Downtime Exclusions:</strong> Excused Downtime shall not be counted as downtime and includes: (a) scheduled maintenance windows notified at least 48 hours in advance (conducted between 01:00 AM – 04:00 AM IST); (b) force majeure events (natural disasters, civil unrest, warfare); (c) upstream telecom carrier, SIP trunk, or SMS/WhatsApp gateway failures; (d) failures of Client’s local ISP, LAN, or client-side hardware; and (e) emergency security patching to mitigate zero-day threats.
    </p>

    <h3>2. Incident Severity Classification & Response Matrix</h3>
    <table>
      <thead>
        <tr>
          <th>SEVERITY LEVEL</th>
          <th>DEFINITION & CRITERIA</th>
          <th>RESPONSE SLA</th>
          <th>TARGET WORKAROUND / FIX</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong style="color: var(--danger);">P1 — Critical Blocker</strong></td>
          <td>Complete platform outage, emergency/casualty system down, billing entirely halted, or database unreachable with no workaround.</td>
          <td><strong>15 Minutes</strong><br>(24x7 Escorted)</td>
          <td><strong>4 Hours</strong><br>(Continuous War-Room)</td>
        </tr>
        <tr>
          <td><strong style="color: var(--warning);">P2 — High Impact</strong></td>
          <td>Core clinical or operational module impaired (e.g., OPD consultations or pharmacy dispensing malfunctioning), causing significant operational disruption.</td>
          <td><strong>1 Hour</strong><br>(Business Hours)</td>
          <td><strong>8 Hours</strong><br>(Same Business Day)</td>
        </tr>
        <tr>
          <td><strong style="color: var(--accent);">P3 — Medium</strong></td>
          <td>Non-critical feature malfunction (e.g., automated WhatsApp reminder delay, report export glitch) with acceptable manual workaround available.</td>
          <td><strong>4 Hours</strong><br>(Business Hours)</td>
          <td><strong>24 Hours</strong><br>(Next Business Day)</td>
        </tr>
        <tr>
          <td><strong style="color: var(--text-muted);">P4 — Minor Inquiry</strong></td>
          <td>General configuration inquiries, cosmetic UI questions, report formatting requests, or non-urgent feature enhancements.</td>
          <td><strong>1 Business Day</strong></td>
          <td>Next Scheduled Sprint Release</td>
        </tr>
      </tbody>
    </table>

    <h3>3. Disaster Recovery Commitments (RPO & RTO)</h3>
    <ul style="margin-left: 20px; margin-bottom: 14px;">
      <li><strong>Recovery Point Objective (RPO):</strong> Maximum <strong>one (1) hour</strong>. Continuous PostgreSQL Write-Ahead Log (WAL) archiving ensures minimal transactional data exposure.</li>
      <li><strong>Recovery Time Objective (RTO):</strong> Maximum <strong>four (4) hours</strong> to restore full production operational capability from snapshot backups in an alternate availability zone.</li>
    </ul>

    <h3>4. Tiered Service Credit Schedule</h3>
    <p>
      If Provider fails to achieve the 99.5% Monthly Uptime commitment in any calendar month, Client shall be entitled to claim Service Credits against future monthly subscription invoices as follows:
    </p>
    <table>
      <thead>
        <tr>
          <th>MONTHLY UPTIME PERCENTAGE</th>
          <th>SERVICE CREDIT APPLIED TO MONTHLY BILLING VALUE</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>99.0% to 99.49%</td>
          <td><strong>5% Service Credit</strong> of monthly equivalent subscription fee</td>
        </tr>
        <tr>
          <td>98.0% to 98.99%</td>
          <td><strong>10% Service Credit</strong> of monthly equivalent subscription fee</td>
        </tr>
        <tr>
          <td>Below 98.0%</td>
          <td><strong>25% Service Credit</strong> of monthly equivalent subscription fee</td>
        </tr>
      </tbody>
    </table>
    <p style="font-size: 11.5px; color: var(--text-light);">
      Service Credits represent Client’s sole and exclusive financial remedy for downtime and must be claimed within thirty (30) days of the end of the affected month.
    </p>
  </section>

  <!-- SECTION 6: PART V — END-USER ACCEPTABLE USE & TERMS OF SERVICE (EULA) -->
  <section id="part-5" class="page-break">
    <h2>
      <span>Part V — End-User Acceptable Use & Terms of Service (EULA)</span>
      <span class="section-tag">User EULA</span>
    </h2>

    <p>
      These End-User Acceptable Use Terms bind every authorized user (doctors, consultants, nurses, administrative staff, billing cashiers, and receptionists) granted credentials to access the Platform under Client’s tenant account.
    </p>

    <h3>1. Individual Accountability & Non-Sharing of Credentials</h3>
    <p>
      1.1 <strong>Strictly Personal Logins:</strong> Each staff account, username, password, and two-factor authentication token is personal and confidential to the assigned individual. <strong>Credential sharing or group-login practices are strictly prohibited.</strong>
    </p>
    <p>
      1.2 <strong>Automatic Session Locks:</strong> Users must not disable or tamper with automated screen timeout locks or session safeguards implemented to protect unattended clinical workstations.
    </p>

    <h3>2. Prohibition on Security Evasion & Spoofing</h3>
    <p>
      2.1 <strong>Anti-Tampering Rules:</strong> End users shall not: (a) attempt to bypass IP address restrictions, device binding locks, or browser devtools deterrents; (b) spoof camera feeds, liveness checks, or biometric sensors; (c) mock or fake GPS geolocation signals when logging field visits; or (d) introduce malware, automated scraping scripts, or security penetration tools.
    </p>
    <p>
      2.2 <strong>Digital Signature Integrity:</strong> Doctors and clinical consultants shall use their digital signature images and medical registration numbers exclusively for bona fide clinical prescriptions and reports authored personally by them. Stamping another practitioner’s seal or signature constitutes fraud and an ethical violation.
    </p>

    <h3>3. Medical Secrecy, NABH Protocols & Statutory Ethical Conduct</h3>
    <p>
      Users shall strictly maintain patient medical confidentiality in compliance with the Indian Medical Council (Professional Conduct, Etiquette and Ethics) Regulations, 2002 and NABH standards. Users shall not capture screenshots of clinical records on personal mobile phones, export patient contact lists for personal use, or disclose patient medical diagnoses outside authorized hospital care teams.
    </p>

    <h3>4. System Monitoring & Evidentiary Value of Audit Logs</h3>
    <p>
      Users acknowledge that all interactions with the Software—including record searches, chart reviews, emergency break-glass accesses, billing adjustments, and prescription printouts—are logged permanently in immutable audit records. Such electronic records constitute valid evidence under Section 65B of the Indian Evidence Act, 1872 / Bharatiya Sakshya Adhiniyam, 2023.
    </p>
  </section>

  <!-- SECTION 7: EXECUTION & SIGNATURE BLOCKS -->
  <section id="execution" class="page-break">
    <h2>
      <span>Execution & Signatory Acceptance</span>
      <span class="section-tag">Binding Execution</span>
    </h2>

    <p>
      IN WITNESS WHEREOF, the Parties hereto have caused this Master Client Legal Agreement Suite (comprising Commercial Order Schedule A, Part I MSA, Part II On-Premise Addendum, Part III DPA, Part IV SLA, and Part V EULA) to be duly executed by their authorized representatives as of the Effective Date written above.
    </p>

    <div class="signature-grid">
      <!-- Licensor / Provider Card -->
      <div class="sig-card">
        <div class="sig-card-header">
          <span>FOR THE PROVIDER:</span>
          <span style="font-size: 11px; color: var(--primary);">Polynexus Technologies</span>
        </div>
        <div><strong>POLYNEXUS TECHNOLOGIES PRIVATE LIMITED</strong></div>
        <div class="sig-field">CIN: U72900MH2024PTC428912</div>
        <div class="sig-line"></div>
        <div class="sig-field"><strong>Authorized Signatory:</strong> <span contenteditable="true">Aniket Suryawanshi</span></div>
        <div class="sig-field"><strong>Designation:</strong> <span contenteditable="true">Chief Executive Officer</span></div>
        <div class="sig-field"><strong>Official Email:</strong> admin@polynexus.in</div>
        <div class="sig-field"><strong>Date of Signing:</strong> <span contenteditable="true">{execution_date_str}</span></div>
        <div class="sig-field" style="margin-top: 14px;"><strong>Company Seal / Stamp:</strong> [SEAL]</div>

        <div style="margin-top: 24px; border-top: 1px dashed var(--border); padding-top: 12px;">
          <div style="font-weight: 600; font-size: 11.5px; color: var(--text-muted);">IN THE PRESENCE OF WITNESS 1:</div>
          <div class="sig-line" style="margin-top: 20px;"></div>
          <div class="sig-field">Name: <span contenteditable="true">Rahul Sharma</span></div>
          <div class="sig-field">Address: <span contenteditable="true">Tech Zone, Nagpur, MH</span></div>
        </div>
      </div>

      <!-- Licensee / Client Card -->
      <div class="sig-card">
        <div class="sig-card-header">
          <span>FOR THE CLIENT:</span>
          <span style="font-size: 11px; color: var(--accent);">Hospital Licensee</span>
        </div>
        <div><strong contenteditable="true">{hospital.name.upper()}</strong></div>
        <div class="sig-field">Clinical Reg: <span contenteditable="true">REG-{hospital.slug.upper()[:6]}-2026</span></div>
        <div class="sig-line"></div>
        <div class="sig-field"><strong>Authorized Signatory:</strong> <span contenteditable="true">Dr. Arvind Deshmukh</span></div>
        <div class="sig-field"><strong>Designation:</strong> <span contenteditable="true">Medical Director & Trustee</span></div>
        <div class="sig-field"><strong>Official Email:</strong> <span contenteditable="true">admin@{hospital.slug}.hms.polynexus.in</span></div>
        <div class="sig-field"><strong>Date of Signing:</strong> <span contenteditable="true">{execution_date_str}</span></div>
        <div class="sig-field" style="margin-top: 14px;"><strong>Hospital Seal / Stamp:</strong> [SEAL]</div>

        <div style="margin-top: 24px; border-top: 1px dashed var(--border); padding-top: 12px;">
          <div style="font-weight: 600; font-size: 11.5px; color: var(--text-muted);">IN THE PRESENCE OF WITNESS 2:</div>
          <div class="sig-line" style="margin-top: 20px;"></div>
          <div class="sig-field">Name: <span contenteditable="true">Pooja Deshmukh</span></div>
          <div class="sig-field">Address: <span contenteditable="true">{hospital_address}</span></div>
        </div>
      </div>
    </div>

    <div class="doc-footer">
      <div>Polynexus Hospital CRM & Healthcare ERP Platform · Confidential</div>
      <div>Page 1 of 1 · Master Agreement Suite</div>
      <div>Governed by Laws of India (DPDP Act 2023 · IT Act 2000)</div>
    </div>
  </section>

</div>

</body>
</html>
"""
    return html

