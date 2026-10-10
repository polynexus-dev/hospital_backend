# Delivering to a client

| Part | What happens | Who | Where |
|---|---|---|---|
| 1. Build | Make the install bundle | You | Your PC (WSL) |
| 2. Deliver | Send the bundle to the hospital | You | USB drive or link |
| 3. Install | First run of the installer | Hospital IT | Client's server |
| 4. Licence | Request and approve the licence | Licence team + SaaS Owner | SaaS console |
| 5. Activate | Second run of the installer | Hospital IT | Client's server |
| 6. Domain | Put it on hms.hospital.com with HTTPS | Hospital IT | Client's server |
| 7. Backups | Send nightly backups off the server | Hospital IT | Client's server |
| 8. Later | Updates and renewals | Both | |

**You never copy code to the client.** Everything is built on your PC and only the finished bundle goes to the hospital. The bundle holds the compiled program (no source code) and the installers. The client's server needs only **Docker** — no Python, Node or source code — and can run **Linux** or **Windows**.

**One bundle serves every client.** What differs per hospital is only its licence file: features, number of users, expiry date, and the machine it's bound to.

**Never send:** the `Backend` or `Frontend` folders, `venv`, any `.env` file, or your private signing key (`license_signing_key.pem`).

**The private signing key lives only on the SaaS server**, with one offline backup in a vault. Licences are created only in the SaaS console, never on a PC.

## Part 1: Build the bundle

On your PC, with **Docker Desktop running**, open **WSL (Ubuntu)**:

```
# WSL (Ubuntu) on your PC
cd /mnt/e/Aniket/next/Hospital/Hospital/Frontend
git pull
git lfs pull
cd ../Backend
git pull
make bundle VERSION=1.0.1
```

**Before `make bundle`:** in the SaaS console, On-Premise Licences → **Revocation list**, save it as `Backend\apps\licensing\revocations.lic`. Every release carries the list, so a licence you revoked stops working on a server once it upgrades. `make bundle` warns if the file is missing.

- The bundle contains **both** the backend and the frontend (with nginx), plus the PostgreSQL and Redis images.
- `git lfs pull` downloads the 3D anatomy model (168 MB), which is built into the frontend. Without it the build stops and tells you to run it.
- Use a new, higher version number for every release (1.0.1, 1.0.2, 1.1.0, ...).
- The first build takes 15–30 minutes; later builds are faster.
- Result: `Backend\dist\bundle-1.0.1.tar.gz` (about 430 MB).

## Part 2: Deliver the bundle

Send `bundle-1.0.1.tar.gz` to the hospital: USB drive, Google Drive or OneDrive link, or secure file transfer. It's safe to share: it contains no source code and no secrets.

So the hospital can confirm the file arrived complete, send its checksum with it:

```
# WSL (Ubuntu) on your PC
sha256sum dist/bundle-1.0.1.tar.gz
```

The hospital compares it with `Get-FileHash bundle-1.0.1.tar.gz` (Windows) or `sha256sum bundle-1.0.1.tar.gz` (Linux).

## Part 3: Install on the client's server

Hospital IT runs this from the folder where they saved the bundle.

**Linux server** (as root):

```
# Linux server
sudo mkdir -p /opt/hms
sudo tar -xzf bundle-1.0.1.tar.gz -C /opt/hms --strip-components=1
cd /opt/hms
sudo ./install.sh
```

**Windows server** (PowerShell as administrator; Docker Desktop with Linux containers):

```
# Windows server - PowerShell (administrator)
mkdir -Force C:\hms
tar -xzf bundle-1.0.1.tar.gz -C C:\hms --strip-components=1
cd C:\hms
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer asks for the **hostname or IP** staff will use, generates secrets, loads the program (no internet needed), and then prints a **Deployment ID** and a **Machine fingerprint**. It saves both to `config/REQUEST-LICENCE.txt`, then stops.

**The hospital sends you `REQUEST-LICENCE.txt`.**

> The installer creates `.env`, which holds the keys that decrypt patient data. Tell the hospital to back up `.env` and keep it private.

## Part 4: Issue the licence

Licences are created only in the **SaaS console**, by two people, each confirming with a **2FA code** from their authenticator app.

**Who can do what**

| Person | Can |
|---|---|
| SaaS Owner | Request and sign licences, approve others' requests, revoke, manage the licence team |
| Staff an Owner allowed ("May request") | Request licences — nothing is signed until an Owner approves |
| Everyone else (including Platform Admins) | Nothing licence-related |

An Owner allows someone in On-Premise Licences → **Licence team** → *Allow licences*. Every staff account has a permanent code such as **PNX-0007**.

**Step 1 — Request.** Tenants & Subscriptions → the hospital → **📜 License**:

| Field | What to enter |
|---|---|
| Deployment ID | **Paste from their REQUEST-LICENCE.txt.** Never leave it blank. |
| Bind to one machine | Ticked, then **paste the fingerprint from REQUEST-LICENCE.txt** |
| Expires on, grace period | From the contract (grace usually 14 days) |
| Max users, max beds, tier | From the contract |
| Licensed features | Tick the purchased features |
| 2FA code | Current 6-digit code from your authenticator app |

Press **Request / Generate License**. If you are a SaaS Owner the licence is signed and downloads at once. Otherwise it says *Sent to a SaaS Owner for approval*, and the Owners get an email.

**Step 2 — Approve (SaaS Owner).** On-Premise Licences → **Licence requests** → check the terms and that the hospital has paid → **Approve** → 2FA code. The `.lic` file downloads. Owners get an email for every licence issued, and the register flags any licence with **No paid invoice**.

The licence has the requester's and approver's staff codes signed inside it. The hospital sees them in **Settings → License → Issued by**. Anyone can check a file (no key needed):

```
REM Command Prompt on your PC
cd /d E:\Aniket\next\Hospital\Hospital\Backend\tools
..\venv\Scripts\python.exe -m license_issuer verify license-city-hospital.lic
```

It must say **Signature OK**, show `Issued by : PNX-.... (approved by PNX-....)`, and its Deployment ID and fingerprint must match `REQUEST-LICENCE.txt`. Then **send the `.lic` file to the hospital.** It is safe to email: it can't be edited and only works on their server.

**When a staff member leaves:** Licence team → **Block** → 2FA code. Their sessions end immediately on every device, their licence right is removed and their pending requests are cancelled. Search the register for their code to review every licence they issued, and revoke any that shouldn't exist.

## Part 5: Activate

Hospital IT saves the licence as `config/license.lic` in the install folder and runs the installer again.

**Linux:**

```
# Linux server
sudo cp license-city-hospital.lic /opt/hms/config/license.lic
cd /opt/hms
sudo ./install.sh
```

**Windows:**

```
# Windows server - PowerShell (administrator)
Copy-Item .\license-city-hospital.lic C:\hms\config\license.lic
cd C:\hms
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer finds the licence, prepares the database, asks for the **administrator email and password**, creates the hospital, and starts everything. It ends by printing the address to open, e.g. `http://hms.cityhospital.local/` (or with `:8080` if port 80 was busy).

The hospital signs in, and checks **Settings → License** shows the purchased features, users and expiry.

## Part 6: Domain and HTTPS

So staff open `https://hms.hospital.com` instead of an IP address.

1. **DNS:** the hospital's IT points the name at the server. For use inside the hospital (recommended), add it to the internal DNS (Windows Server DNS or the router) with the server's LAN IP. For access from outside, add a public DNS record and forward ports 80 and 443 to the server — a VPN is safer than exposing a hospital system to the internet.
2. **Run the domain script** in the install folder:

```
# Linux server
sudo ./configure-domain.sh
```

```
# Windows server - PowerShell (administrator)
powershell -ExecutionPolicy Bypass -File .\configure-domain.ps1
```

It asks for the domain, then how to do HTTPS:

| Choice | When to use it |
|---|---|
| 1. Hospital's own certificate | The hospital has a certificate (e.g. `*.hospital.com`). Give the PEM certificate and key files; they are checked before use. |
| 2. Let's Encrypt | Free, renews itself automatically. The server must be reachable from the internet on port 80. |
| 3. No certificate | Plain HTTP, internal network only, until a certificate is available. |

It updates the settings and restarts. Use ports 80 and 443 for a domain: if the installer moved to 8080 because port 80 was busy, free port 80 first (see RUNBOOK section 8).

## Part 7: Backups

Every night at 02:00 the system backs up the whole database and uploaded documents into the `backups` folder, keeps 30 days, and writes the result to `backups/LAST-BACKUP.txt`.

**Backups must leave the server**, or a disk failure loses them too. Hospital IT sets `BACKUP_DIR` in `.env` to a NAS or external drive and restarts the backup service:

```
# .env in the install folder (one of these)
BACKUP_DIR=/mnt/nas/hms-backups
BACKUP_DIR=D:/hms-backups
```

```
# Linux or Windows server, in the install folder
docker compose up -d backup
docker compose exec backup sh /backup.sh now
```

The second line makes a backup immediately, to confirm it works. Then:

- **Weekly:** open `LAST-BACKUP.txt` and check it says `OK`.
- **Once after installing:** practise a restore (RUNBOOK section 9).
- **Keep a copy of `.env` separately and safely:** backups can't be read without its keys.

## Part 8: Updates and renewals

**New version of the software:**

1. You build a new bundle with a higher version (Part 1) and deliver it (Part 2).
2. Hospital IT unpacks it into a **new, separate folder** and runs the upgrade from there:

```
# Linux server (from the new bundle folder)
sudo ./upgrade.sh /opt/hms
```

```
# Windows server - PowerShell (administrator), from the new bundle folder
powershell -ExecutionPolicy Bypass -File .\upgrade.ps1 -InstallDir C:\hms
```

The upgrade **backs up the database first**, then updates and restarts. The licence, settings and all data are kept.

**Licence renewal** (yearly, or when they buy more users or features): issue a new licence for the same hospital as in Part 4 (request, then Owner approval) with the new expiry, users or features, and the same deployment ID and fingerprint. Ask the hospital for a usage report first (Settings → License → **Download usage report**) and upload it in On-Premise Licences: it shows their real number of users. If the report names a licence you never issued, the upload is refused and the Owners get a security alert.

Send the new file. The hospital admin uploads it in **Settings → License** — it applies immediately, no restart.

**Full reference:** `deploy/RUNBOOK.md` (troubleshooting, HTTPS, rollback). **Practice run:** `docs/TRIAL_INSTALL_WINDOWS.pdf`.
