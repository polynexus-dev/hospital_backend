# Source code management procedure

**NABH Digital Health Standards for HIS/EMR, DOM.1.f:** *"The source code management processes are defined and practised by the HIS/EMR vendor."*

| | |
|---|---|
| Owner | Engineering lead, Polynexus |
| Applies to | `polynexus-dev/hospital_backend`, `polynexus-dev/hospital_frontend`, and the on-premise install bundle built from them |
| Review | Every 12 months, or after any security incident involving the code |
| Status | **Draft: approve and sign before an assessment.** Items marked *(proposed)* are not yet enforced in GitHub. |

## 1. Repositories and access

- All source is held in private GitHub repositories under the `polynexus-dev` organisation. Nothing is developed outside them.
- Access is per person, never shared. Organisation members must use two-factor authentication on GitHub *(proposed: enforce in organisation settings)*.
- Write access is limited to the engineering team. Contractors get access to a single repository for the period of their contract, and lose it the day the contract ends.
- An access review happens every quarter. The engineering lead removes anyone who has left or no longer needs access, and records the review.

## 2. Branches

| Branch | Purpose | Who merges |
|---|---|---|
| `main` | What is deployed to SaaS and built into on-premise bundles | Only through a reviewed pull request |
| `feature/<short-name>` | One piece of work (e.g. `feature/dual-mode-licensing`) | The author, after review |
| `hotfix/<ticket>` | An urgent production fix | The author, after review (can be fast-tracked; see §5) |

- `main` is protected *(proposed: turn on branch protection)*: no direct pushes, no force-pushes, pull requests need one approving review and green CI.
- Commits describe *why* a change was made. Commits co-written with an AI assistant carry a `Co-Authored-By` line.

## 3. Review and automated checks

Every pull request must pass the CI pipelines in `.github/workflows/ci.yml` before it is merged:

| Check | Backend | Frontend |
|---|---|---|
| Automated tests | pytest, about 1,000 tests on PostgreSQL 16 | Vitest unit tests; Playwright cross-browser suite (Chrome, Edge, Firefox, Safari/WebKit, phone sizes) |
| Static checks | Django system checks, migrations check | ESLint, TypeScript type-check, production build |
| Dependency vulnerabilities | `pip-audit` (blocking) | `npm audit --audit-level=high` (blocking) |

A reviewer checks the following:
1. **Correctness:** the change does what the ticket says, and there are tests for it.
2. **Security:** no secrets in code, permissions are checked, and every query is limited to the user's hospital.
3. **Patient data:** identifying fields stay encrypted, and audit logging is kept.
4. **NABH traceability:** if the change implements or alters an NABH element, the code and its test carry the element tag (e.g. `# NABH AAC.1.b`), and `docs/nabh/build_matrix.py` is re-run so `COMPLIANCE_MATRIX.md` stays current.

## 4. Secrets

- Secrets never go into a repository: `.env` files are git-ignored, and `.env.example` holds names only.
- Production secrets live only on the server they are used on (SaaS: the VM's `Backend/.env` and `/etc/polynexus`). The licence signing key never leaves the SaaS server.
- If a secret is committed by mistake, it is **rotated immediately**. Deleting the commit isn't enough, because the history keeps it. The rotation is recorded in the incident register.

## 5. Releases

1. Changes are merged into `main`.
2. SaaS: Jenkins pulls `main` onto the VM and runs `docker compose up -d`. Migrations and start-up tasks run automatically.
3. On-premise: a bundle is built from a tagged `main` commit. It contains Cython-compiled backend modules and obfuscated frontend code, so customers receive no readable source. The bundle version is bumped (e.g. 1.0.1 → 1.0.2) and release notes are published to users through the in-app release notes (NABH DOM.2.a).
4. Every release is tagged in git (`vX.Y.Z`) *(proposed)*, so any bundle can be traced back to the exact source it was built from.
5. **Hotfixes:** these can be released with one reviewer instead of the usual review. They still go through CI, and a follow-up ticket records why the fix was urgent.

## 6. Protecting the source

- Customers receive compiled and obfuscated builds only.
- The repositories are backed up off GitHub every week *(proposed: a scheduled mirror to company storage)*.
- If GitHub is unavailable, any engineer's up-to-date clone plus the mirror can restore the repositories.

## 7. Records kept

| Record | Where |
|---|---|
| Who changed what and why | Git history, pull requests |
| Review approvals and CI results | GitHub pull requests and Actions |
| Releases | Git tags, in-app release notes |
| Access reviews | Engineering lead's quarterly review log |
| Secret rotations | Incident register (`governance` app) |
