# Polynexus HMS — Operations Runbook

How to issue a licence, build the install bundle, install on a server with no
internet (Linux or Windows), renew a licence, and fix licence problems.

| Who does it | Section |
|---|---|
| Polynexus (once) | [1. One-time setup](#1-one-time-setup-polynexus) |
| Polynexus (each release) | [2. Build the bundle](#2-build-the-bundle-polynexus) |
| Polynexus (each customer) | [3. Issue a licence](#3-issue-a-licence-polynexus) |
| Hospital IT | [4. Install on a new server](#4-install-on-a-new-server-hospital-it) |
| Both | [5. Renew](#5-renew-a-licence), [6. Upgrade](#6-upgrade-to-a-new-version), [7. Troubleshoot](#7-troubleshooting) |
| Hospital IT | [8. Domain and HTTPS](#8-domain-and-https), [9. Backups and restore](#9-backups-and-restore) |

---

## How it fits together

- **One codebase, two modes.** `DEPLOYMENT_MODE=on_premise` (the bundle) or `saas` (our hosting). On-premise images are fixed to on-premise mode when they're built, so licensing can't be switched off with an environment variable.
- **The licence** is a signed file (`license.lic`, Ed25519). Polynexus signs it with a private key that never leaves us; the product checks it **offline** with the public key compiled into the image. It states the customer, the **deployment ID** of the installation, start and expiry dates, the grace period, the maximum number of active users, the **features**, and (optionally) the **machine fingerprint** it's bound to.
- **Checked** when the server starts (a licence for another machine stops it from starting), on every request (cached for 10 minutes), and hourly by Celery beat.
- **When it expires:** during the grace period everything still works and a red banner warns. After the grace period the system becomes **read-only**: records can be viewed and printed, nothing can be changed. Data is never deleted.
- **Features → menus and APIs.** Each feature switches on a group of modules (for example ERP: inventory, pharmacy, finance, HR). Unlicensed modules disappear from the menu and their APIs refuse requests. Integrations that leave the building (WhatsApp, IVR, ABDM) stay offline unless their feature is licensed.

| Feature | Unlocks |
|---|---|
| HMS core | OPD, IPD, nursing, beds, billing, emergency, OT, ICU, lab, radiology, blood bank, queue, dietary, MRD, ... |
| CRM | Enquiries, call queue and callbacks, referrals, packages, feedback, workflows, TPA desk |
| ERP | Inventory, pharmacy, finance and accounts, HR, support services |
| MIS | Predictive analytics and reports |
| WhatsApp messaging | Inbox and outbound WhatsApp |
| IVR integration | Connection to the hospital's PBX |
| AI assist | The assistant and AI auto-replies (local models only) |
| NABH reporting | Quality and NABH KPIs |
| Multi-branch | More than one hospital in an installation |
| Patient portal | Patient portal |
| API access | ABDM / national health-network APIs |

---

## 1. One-time setup (Polynexus)

1. **Key pair.** Already done (10 Oct 2026). The public key is committed in `apps/licensing/public_key.py`; the private key is `license_signing_key.pem`, stored outside the repo. **Never commit it, never send it to anyone. Never run `generate_license_keypair` again** — a new key pair invalidates every licence ever issued.
2. **Where issuing runs.** Wherever you run the issuer (your laptop, the SaaS server), point it at the private key:
   ```
   LICENSE_SIGNING_KEY_PATH=/secure/path/license_signing_key.pem
   ```

## 2. Build the bundle (Polynexus)

On Linux, macOS, or WSL on Windows, with Docker running, both repos side by side (`Backend/`, `Frontend/`):

```
cd Frontend && git pull && git lfs pull && cd ..   # lfs pull fetches the 3D anatomy model
cd Backend && git pull
git tag v1.0.0            # the bundle is named after the version
make test                 # backend + frontend tests
make bundle               # builds both images, saves them, packs the bundle
```

Result: `dist/bundle-1.0.0.tar.gz` containing `images.tar` (backend, frontend/nginx, PostgreSQL, Redis), `docker-compose.yml`, `install.sh`, `install.ps1`, `upgrade.sh`, `upgrade.ps1`, `env.template`, this runbook and `VERSION`. It is everything an offline server needs.

What the images contain:
- **Backend:** Python 3.12, compiled with Cython — no application source code inside. Non-root, gunicorn, static files collected at build time. Set `COMPILE=0 ON_PREMISE=0` build args for the SaaS image.
- **Frontend:** minified build with our code obfuscated, no source maps, including the 3D anatomy model (the build refuses to run if `git lfs pull` was skipped). Served by nginx, which is also the reverse proxy (`/api` → backend) and turns on HTTPS when certificates are present.

Images are `linux/amd64`. They run on Linux servers and on Windows servers through Docker Desktop / WSL2.

## 3. Issue a licence (Polynexus)

You need, from the hospital: their **deployment ID** and, for a hardware-bound licence, their **machine fingerprint**. The installer prints both and saves them in `config/REQUEST-LICENCE.txt`.

**Option A — command-line wizard** (any machine with the private key):
```
cd Backend/tools
python -m license_issuer new
```
It asks for: legal name, deployment ID (blank generates one — then the hospital must put it in `.env` as `DEPLOYMENT_ID`), start and expiry dates (YYYY-MM-DD), grace days (default 14), maximum active users, features (numbered checklist, all off by default), hardware binding (and the fingerprint), and the output file. It prints a summary to send with the licence.

Check any licence file:
```
python -m license_issuer verify license.lic
```

**Option B — SaaS console:** Tenants & Subscriptions → the hospital → **📜 License**. Same licence format; the issued history, re-download and revoke are there.

Send `license.lic` to the hospital. It's safe to email: it's signed (any edit breaks it) and only works on that deployment.

> **Revoking** in the SaaS console stops re-downloads. An offline server can't be told, so a revoked licence keeps working there until it expires or a newer one replaces it.

## 4. Install on a new server (Hospital IT)

**Server:** 64-bit, 4+ CPU cores, 8+ GB RAM, 100+ GB disk.
- **Linux** (Ubuntu 22.04/24.04 recommended): Docker Engine with the Compose plugin.
- **Windows** (10/11 Pro or Server 2022): Docker Desktop set to Linux containers (WSL2). Windows Server without Docker Desktop: Docker Engine inside WSL2 — then follow the Linux steps inside WSL.

No internet is needed. Copy `bundle-<version>.tar.gz` to the server and unpack it where it should live:

| | Linux | Windows |
|---|---|---|
| Unpack | `sudo mkdir -p /opt/hms && sudo tar -xzf bundle-1.0.0.tar.gz -C /opt/hms --strip-components=1` | `mkdir C:\hms; tar -xzf bundle-1.0.0.tar.gz -C C:\hms --strip-components=1` |
| Install | `cd /opt/hms && sudo ./install.sh` | Admin PowerShell: `cd C:\hms; powershell -ExecutionPolicy Bypass -File .\install.ps1` |

**First run** — the installer:
1. checks Docker, loads the images from `images.tar` (offline);
2. asks for the hostname or IP staff will use, and creates `.env` with fresh random secrets and a new **deployment ID**;
3. starts PostgreSQL and Redis;
4. prints the **deployment ID** and **machine fingerprint** and saves them to `config/REQUEST-LICENCE.txt`. **Send that file to Polynexus**, and press Enter to stop.

**Second run**, once you have `license.lic`: run the installer again and give the licence path (or copy it to `config/license.lic` first). It then creates the database, asks for the **administrator email** and password, activates the licence, creates the hospital, and starts everything. Open `http://<hostname>/` and sign in.

**Back up `.env` now** (and `config/`). Its keys decrypt patient data; losing it means losing the data.

**HTTPS:** put `tls.crt` and `tls.key` in `certs/`, set `SECURE_SSL_REDIRECT=True` in `.env`, then `docker compose restart nginx web`.

## 5. Renew a licence

From 30 days before expiry, administrators see a yellow banner; after expiry a red one (grace period); after the grace period the system is read-only.

1. **Polynexus:** `python -m license_issuer renew license.lic` → enter the new expiry date. The licence ID stays the same; the file is re-signed.
2. **Hospital admin:** Settings → License → drop the new file on the upload box. It applies immediately, no restart. (Or replace `config/license.lic` — it is picked up immediately.)

An older licence can't replace a newer one.

## 6. Upgrade to a new version

Unpack the new bundle in a **separate** folder, then run its upgrade script against the installation:

| Linux | Windows |
|---|---|
| `sudo ./upgrade.sh /opt/hms` | `powershell -ExecutionPolicy Bypass -File .\upgrade.ps1 -InstallDir C:\hms` |

It **backs up the database first** (`backups/hms-<old version>-<time>.dump`, plus `.env` and the licence), loads the new images, updates the deploy files (keeping `.env`, `config/` and all data), migrates, and restarts.

**Roll back:** set `HMS_VERSION` back in `.env`, restore the dump, start:
```
docker compose up -d postgres
docker compose cp backups/hms-<old>-<time>.dump postgres:/tmp/restore.dump
docker compose exec postgres pg_restore -U hms -d hms --clean --if-exists /tmp/restore.dump
docker compose up -d
```

## 7. Troubleshooting

See the licence state: Settings → License, or `GET /api/license/status/`, or on the server:
```
docker compose exec web python manage.py get_machine_fingerprint
docker compose logs web | grep -i licen
```

| Symptom | Cause | Fix |
|---|---|---|
| `web` keeps restarting; log says *"bound to a different server"* | Hardware/OS changed, or the licence is for another machine | Send the fingerprint from the log to Polynexus for a new licence (or issue without hardware binding) |
| Red banner *"read-only archive mode"*; saves fail with 402 | Expired, grace period over | Renew (section 5) |
| *"This license is for deployment X, but this installation is Y"* | `DEPLOYMENT_ID` in `.env` differs from the licence | Put the licence's ID in `.env` (`DEPLOYMENT_ID=`) and restart, or issue a licence for this installation's ID |
| *"The system clock has been set back"* | Server time earlier than a time already recorded | Fix the clock (NTP), then restart `web` |
| *"This license starts on …"* | Start date in the future | Wait, or reissue with an earlier start |
| *"No license is installed"* | `config/license.lic` missing or empty | Upload in Settings → License, or copy the file and restart `web` |
| *"The license signature is not valid"* | File damaged or edited | Download/receive it again; check with `license_issuer verify` |
| A menu / module missing | Feature not licensed | Issue a licence that includes it |
| *"The license allows N active users"* | User cap reached | Deactivate unused accounts, or a licence with a higher cap |
| WhatsApp / IVR / ABDM do nothing | Integration features are offline unless licensed | Licence the feature, then set the provider in `.env` |
| `install.ps1`: *"Docker must run Linux containers"* | Docker Desktop is in Windows-containers mode | Docker Desktop tray → *Switch to Linux containers* |
| A service unhealthy | | `docker compose ps`, then `docker compose logs <service>` |

Never delete the `pgdata` volume or `.env` to "reset" a licence problem — licence problems never require touching data.

## 8. Domain and HTTPS

To reach the system at a name such as `hms.hospital.com` instead of an IP:

1. **DNS first.** Point the name at the server:
   - *Inside the hospital only (recommended):* a record in the hospital's internal DNS (Windows Server DNS or the router) → the server's LAN IP, e.g. `192.168.1.50`.
   - *Also from the internet:* a public DNS record → the hospital's public IP, and the router forwarding ports 80 and 443 to the server. Prefer a VPN to exposing a hospital system to the internet.
2. **Run the domain script** from the install folder:

   | Linux | Windows (PowerShell as administrator) |
   |---|---|
   | `sudo ./configure-domain.sh` | `powershell -ExecutionPolicy Bypass -File .\configure-domain.ps1` |

   It asks for the domain and how to do HTTPS:
   - **1 — the hospital's own certificate:** give the paths of the certificate (PEM, including the intermediate chain) and its private key. nginx checks the pair before it's used; a wrong file changes nothing. A Windows `.pfx` must be converted to PEM first.
   - **2 — Let's Encrypt:** free and **renews automatically** (checked twice a day). Needs the public DNS record, port 80 open from the internet, and `HTTP_PORT=80`.
   - **3 — no certificate for now:** plain HTTP, internal network only.

   It updates `.env` (allowed hosts, HTTPS redirect) and restarts. Staff then open `https://hms.hospital.com/`.

Use the standard ports 80 and 443 for a domain. If the installer moved to 8080 because port 80 was busy (often IIS), free port 80, set `HTTP_PORT=80` and `HTTPS_PORT=443` in `.env`, and run `docker compose up -d`.

When the hospital's own certificate is replaced, run the script again — or overwrite `certs/tls.crt` and `certs/tls.key`; nginx notices within an hour, no restart needed.

## 9. Backups and restore

The `backup` service runs every night at **02:00** (server time) and writes to the `backups` folder:

| File | What |
|---|---|
| `hms-db-<date>.dump` | The whole database (checked readable before it's kept) |
| `hms-files-<date>.tar.gz` | Uploaded documents and signatures |
| `license-<date>.lic` | The licence |
| `LAST-BACKUP.txt` | Result of the last run: `OK` or `FAILED` |

Files older than 30 days are deleted. Settings in `.env`: `BACKUP_TIME`, `BACKUP_KEEP_DAYS`, and **`BACKUP_DIR`**.

**Keep backups off the server.** A backup on the same disk is lost with it (disk failure, ransomware). Set `BACKUP_DIR` to a NAS or external drive — e.g. `BACKUP_DIR=/mnt/nas/hms-backups` (Linux) or `BACKUP_DIR=D:/hms-backups` (Windows) — then `docker compose up -d backup`. Also keep a copy of `.env` somewhere safe and separate: backups can't be read without its keys.

**Check it:** open `backups/LAST-BACKUP.txt` weekly. **Back up right now** (e.g. before maintenance):
```
docker compose exec backup sh /backup.sh now
```

**Restore** a dump (this replaces the current data — stop the app first):
```
docker compose stop web worker beat
docker compose cp backups/hms-db-<date>.dump postgres:/tmp/restore.dump
docker compose exec postgres pg_restore -U hms -d hms --clean --if-exists /tmp/restore.dump
docker compose up -d
```
Restore uploaded files:
```
docker compose run --rm --no-deps --entrypoint sh -v hms_media:/restore backup -c "tar -xzf /backups/hms-files-<date>.tar.gz -C /restore"
```

**Practise a restore** once after installation, into a scratch database, so you know the backups work:
```
docker compose cp backups/hms-db-<date>.dump postgres:/tmp/r.dump
docker compose exec postgres sh -c "createdb -U hms restore_test && pg_restore -U hms -d restore_test --no-owner /tmp/r.dump; psql -U hms -d restore_test -c 'select name from core_hospital'; dropdb -U hms restore_test"
```
It should list your hospital's name.
