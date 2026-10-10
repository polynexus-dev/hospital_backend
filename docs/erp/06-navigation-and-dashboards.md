# Navigation & Dashboards

## Current state (frontend, `Frontend/src/app/navConfig.ts`, `Shell.tsx`) — superseded, see §1

The paragraph below describes the pre-switcher state this section was originally written against; kept for history. As of the domain-switcher ship (§1), the sidebar is domain-aware and per-item-permission-gated, and `/admin` is both nav-hidden (`requiredPermission: "accounts.view_role"`) and route-guarded (`RequirePermission` in `App.tsx`).

One flat `allNav` list (`dailyWorkNav` + `growthNav`), rendered identically for every logged-in user — confirmed during the recent security review that this has no role-based filtering at all (`/admin` is reachable client-side by any authenticated user regardless of role; enforcement is left entirely to the backend). That gap needs closing regardless of the ERP build — see §3.

## 0. Phase 3 → Phase 4 status: a third nav section, not yet a full switch

**Phase 3**: OPD needed no new nav surface at all — the OPD queue screen already existed (`Frontend/src/features/appointments/AppointmentsPage.tsx`, pre-dating this plan), and the only missing piece, clinical-content capture, shipped as `ConsultationPanel` embedded in the existing appointment detail modal. No new route, no new nav item.

**Phase 4**: IPD had no pre-existing screen to embed into — this is the "second ERP-only screen" that was always going to be the threshold for adding real ERP navigation (see the Phase 3 version of this note). What shipped is **smaller than the full domain switcher in §1**: a third sidebar section, "Ward care" (`Frontend/src/app/navConfig.ts`'s `erpNav`, rendered conditionally in `Shell.tsx` — only appears at all if the logged-in user has `ipd.view_admission`), currently holding one item, `/ipd`. This is a real step toward §1, not the same thing as it — there's still no CRM/ERP *toggle*, just CRM nav, Growth nav, and now Ward care nav all visible together to a user with `domain=both` access. The toggle in §1 becomes worth building once there are enough ERP sections that showing all of them at once gets crowded — not yet, at one item.

The IPD screen itself (`Frontend/src/features/ipd/IPDPage.tsx`) covers: a bed board (wards → rooms → beds, colored by status), the admission list, an "Admit patient" flow, and — inside an admission's detail view, `AdmissionDetailPanel.tsx` — doctor progress notes (with finalize), nursing notes, medication administration, the discharge summary (create + finalize), and the discharge action itself. This is where "nursing station" from the original backlog item landed: folded into the admission detail view, same pattern as OPD's consultation content, rather than a separate route — nursing content is scoped to one admission at a time in practice, so a separate cross-cutting "nursing station" screen wasn't obviously more useful than the equivalent section here.

**Not built**: ward-transfer request/approval has no UI (backend is complete — see `02-domain-model.md`); ward/room/bed *creation* has no form either (configuring the physical facility layout currently requires Django admin or direct API calls, not a dedicated screen) — both deliberate scope cuts, not oversights, to keep this phase to the OPD→admission→care→discharge exit criterion.

**Phase 5**: two more `erpNav` items, `/diagnostics` (`laboratory.view_laborder`) and `/pharmacy` (`pharmacy.view_medicine`). **Shipped correction to §3's planned tree below**: `laboratory` and `radiology` shipped as one combined **Diagnostics** screen with an in-page Lab/Radiology tab switch (`Frontend/src/features/diagnostics/DiagnosticsPage.tsx`), not two separate nav leaves under a "Diagnostics" group heading — they share an identical order→result/report→verify shape and the same "which patient" entry point, so two nearly-identical routes would have meant near-duplicate screens for no navigational benefit at four total ERP items. `pharmacy` shipped as its own screen (`PharmacyPage.tsx`: catalogue + low-stock flagging, dispense-against-a-batch modal, recent-dispensing feed) as planned. Still no CRM/ERP domain-switcher toggle — five ERP items now visible at once (`ipd`, `diagnostics`, `pharmacy`, plus whatever of `emergency`/`ot`/`icu`/`bloodbank` the user's role grants) is getting closer to the "crowded" threshold §1 names as the trigger to finally build it, worth revisiting next phase.

**Post-Phase-5**: the domain-switcher threshold this note keeps flagging was crossed — see §1, now marked SHIPPED. Turned out three more things had silently rotted past the point this section describes, found while wiring the switcher: `careNav`/`businessNav` (`emergency`/`ot`/`icu`/`bloodbank`/`finance`/`billing`/`hr`/`inventory`) were defined in `navConfig.ts` but never imported into `Shell.tsx` at all — not "crowded," literally not rendered; none of those pages (nor `/settings`) had a route in `App.tsx` either, so they were unreachable even by typing the URL; and `UserSerializer` never actually serialized `permissions`/`role_domain`/`hospital_enabled_modules` on `GET /users/me/` despite this doc and the frontend type both assuming it did since Phase 2 — every permission/domain/module check in the frontend had been silently evaluating against `undefined`. All fixed as part of the same change; see §1's "Shipped" note for what actually ships.

## 1. Top-level domain switch — SHIPPED

Domain switcher above the sidebar groups, driven by `Role.domain` (`03-rbac-and-roles.md` §2d) and, separately, whether the hospital's subscription (`Hospital.enabled_modules`) includes at least one real ERP module — not a static list:

```
[ CRM ]  [ HMS ]        <- only shown when role.domain grants both AND the hospital has ≥1 ERP module enabled
```

`Frontend/src/app/Shell.tsx`: `canSeeCRM`/`canSeeERP`/`showDomainSwitcher` derived from `user.role_domain` + `user.hospital_enabled_modules` (both newly exposed on `GET /users/me/` — `UserSerializer` never actually serialized them before this shipped, despite the frontend type and `hasNavAccess`/`RequirePermission` already reading them; that was the root blocker). Active domain persists client-side (`localStorage`, key `hms_active_domain`) — switching is instant, no reload, no re-auth. A `crm_executive` (domain=crm) never sees the switch — their nav is CRM-only, permanently. A `doctor` (domain=erp) never sees CRM nav (assuming the hospital's subscription enables at least one ERP module — an erp-domain role at a CRM-only-tier hospital sees an empty-but-not-crashing ERP nav otherwise). An `erp_super_admin`/`hospital_administrator` (domain=both) gets the switch, gated on the hospital actually running ERP. This directly implements the spec's "separate navigation... clear domain ownership" without hardcoding two logins — one authenticated session, nav scoped by role.

**Shipped correction — "ERP" is labeled "HMS" in the actual UI** (💼 CRM / 🏥 HMS pill), matching the product-naming the switcher was speced against; functionally identical to the `[ CRM ] [ ERP ]` sketch above.

**Known gap, not built here**: no UI to *set* `Role.domain` — every role defaults to `both` and no permission template sets it narrower; the only way to lock a role to `crm`/`erp` today is Django admin (`AdminPage.tsx`'s Roles tab is list-only, no create/edit form for any Role field). Same gap for `Hospital.enabled_modules` — `Frontend/src/api/hospitals.ts`'s `updateHospitalModules()`/`toggleHospitalStatus()` helpers exist but call backend endpoints (`/hospitals/{id}/update-modules/`, `/toggle-status/`) that don't exist anywhere in `Backend/`; dead client code, not wired to any screen. Until one of these ships, both stay Django-admin/shell-only configuration, same as several other capability flags this doc set already documents that way.

## 2. CRM navigation (existing, restructured under the switch)

```
CRM
├─ Daily Work
│  ├─ Console          (existing: /console)
│  ├─ Callbacks         (existing: /callbacks)
│  ├─ Enquiries          (existing: /enquiries)
│  ├─ Appointments       (existing: /appointments — CRM-facing "request" view once Phase 3 splits it, see below)
│  ├─ Inbox             (existing: /inbox)
│  └─ Dashboard          (existing: /dashboard)
└─ Growth
   ├─ Referrals          (existing: /referrals)
   ├─ Packages/Corporate (existing: /packages)
   ├─ Feedback            (existing: /feedback)
   ├─ Campaigns           (NEW — surfaces packages.Campaign ROI reporting, Phase 2)
   └─ Admin               (existing: /admin, CRM-scoped roles/users only)
```

`/patients` (currently under `dailyWorkNav`) splits: CRM roles keep a **lightweight** patient list (name, contact, last interaction — the `PatientLookupSerializer`-style view already established), ERP roles get the full clinical patient record. Same route, role-gated serializer (`03-rbac-and-roles.md` §2c) — not two separate pages to maintain.

`/tpa` moves from `growthNav` to ERP nav once `billing` ships (Phase 7) — insurance/claims work is operationally an ERP/billing concern even though it's CRM-adjacent today for lack of anywhere else to put it.

## 3. ERP navigation (new)

```
ERP
├─ Front Desk
│  ├─ Registration        (patients — full record)
│  ├─ OPD Queue            (opd, Phase 3)
│  └─ Appointments          (ERP-side scheduling view)
├─ Clinical
│  ├─ My Patients           (doctor/nurse assigned-scope view)
│  ├─ IPD / Admissions       (ipd, Phase 4)
│  ├─ Nursing Station        (nursing, Phase 4)
│  ├─ Emergency              (emergency, Phase 6)
│  ├─ OT / Surgery            (ot, Phase 6)
│  └─ ICU                     (icu, Phase 6)
├─ Diagnostics                 (laboratory + radiology, Phase 5 — shipped as one combined screen with a Lab/Radiology tab, not two leaves, see §0)
├─ Pharmacy & Inventory
│  ├─ Pharmacy                 (pharmacy, Phase 5, shipped)
│  ├─ Inventory                 (inventory, Phase 7)
│  └─ Blood Bank                (bloodbank, Phase 6)
├─ Finance
│  ├─ Billing                   (billing, Phase 7)
│  ├─ Insurance / TPA           (tpa, extended, Phase 7)
│  └─ Accounts                  (finance, Phase 8)
├─ HR
│  └─ Employees                 (hr, Phase 8)
└─ ERP Dashboard                (analytics, extended)
```

Every leaf item's visibility is `role.has_perm(view_<model>)` — the nav config becomes data-driven off the permission set rather than hardcoded per-role `if` branches, same principle as the domain switch above.

## 4. Dashboards

### CRM Dashboard (extend existing `analytics.DailyMISLog` + `/dashboard` page)

- Total/new leads, conversion rate, appointment conversion
- Lead source & campaign performance (needs `packages.Campaign` ROI fields, Phase 2)
- Referral performance (`referrals`)
- Follow-ups pending, missed appointments
- Complaints (`feedback.Complaint`)
- Corporate account summary (`packages.CorporateClient`)
- Revenue attribution — **note:** this needs a `billing`→CRM read (which lead/campaign produced how much billed revenue). That's a legitimate cross-domain *reporting* query, not a coupling violation — reporting is allowed to read across the boundary (it's not mutating anything, and `analytics` already exists specifically for this kind of read-only aggregation), unlike the operational apps in `05-integration-architecture.md`, which must go through events. Implement as a scheduled aggregation job into `analytics`-owned summary tables, not a live cross-app join on every dashboard load.

### ERP Ops Dashboard (grows per phase — not yet its own page, see §0)

- OPD (**shipped, Phase 3**): encounters today, waiting, in consult, completed today — `apps.analytics.services.opd_snapshot()`, `GET /api/v1/reports/opd-snapshot/`, rendered as a stat row on the existing CRM `DashboardPage.tsx`, gated on holding any `opd.*` permission. Queue length/wait-time-average weren't added — `opd_snapshot` counts by `Appointment.status`, which is enough for the four numbers above without duplicating what `doctor_queue()` (pre-existing, apps.appointments) already computes per-doctor.
- Bed occupancy (**shipped, Phase 4**): total/occupied/available beds + occupancy % — `apps.analytics.services.bed_occupancy_snapshot()`, `GET /api/v1/reports/bed-occupancy/`, a second stat row gated on holding any `ipd.*` or `facilities.*` permission. Kept as its own function rather than folded into `opd_snapshot` — bed occupancy is IPD/facilities data, not OPD, and the two rows can appear independently depending on which permissions a role actually has.
- IPD: bed occupancy % (overall + per ward), current admissions, today's discharges (Phase 4)
- ICU: ICU bed occupancy, ventilator utilization (Phase 6)
- OT: utilization %, today's schedule, cancellations (Phase 6)
- Lab (**shipped, Phase 5**): orders today, pending orders, average turnaround (order → verified result, minutes) — `apps.analytics.services.lab_tat_snapshot()`, `GET /api/v1/reports/lab-tat/`, gated on any `laboratory.*` permission. **Shipped correction**: no separate critical-result count stat — that signal already surfaces as an urgent `automation.Task` (§05-integration-architecture.md), a dedicated dashboard count would be a second place to look at the same information.
- Pharmacy (**shipped, Phase 5**): total medicines tracked, low-stock count — `apps.analytics.services.pharmacy_low_stock_snapshot()`, `GET /api/v1/reports/pharmacy-low-stock/`, gated on any `pharmacy.*` permission. **Shipped correction**: no "today's dispensing volume" stat — the Pharmacy screen's own "recent dispensing" feed already covers that view; the dashboard row focuses on the one number that needs proactive attention (what's about to run out), not a duplicate activity count.
- Finance: today's collections, outstanding, insurance receivables aging (Phase 7-8)

Same aggregation-table pattern as the CRM dashboard's revenue-attribution note — an ops dashboard hitting live tables across 8 apps on every page load doesn't scale past a handful of concurrent users; each phase adds its own summary table alongside its models, refreshed by a Celery beat task (matching the existing `send-daily-mis-to-owners` pattern already in `config/settings/base.py`'s `CELERY_BEAT_SCHEDULE`).

## 5. The nav-gating gap that exists today, independent of any ERP work

Fix this in Phase 2 regardless: `ProtectedRoute` (`Frontend/src/app/ProtectedRoute.tsx`) currently checks only "is authenticated," not role — `/admin` and (once ERP nav ships) every clinical route would be reachable client-side by any logged-in user. Add a `requiredPermission` field to `NavItem` and a route-level check against the fetched user's permission set (`/users/me/` already returns role info) before rendering the route, not just hiding the nav link. Hiding a nav link without gating the route is security theater — the backend's `RoleBasedModelPermissions` is still the real enforcement, but an ungated frontend route lets an unauthorized user's browser *render* a page shell (and briefly flash data before an API 403 arrives) that a properly gated route wouldn't.
