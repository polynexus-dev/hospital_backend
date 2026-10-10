# Delivering to a client

| Part | What happens | Who | Where |
|---|---|---|---|
| 1. Build | Make the install bundle | You | Your PC (WSL) |
| 2. Deliver | Send the bundle to the hospital | You | USB drive or link |
| 3. Install | First run of the installer | Hospital IT | Client's server |
| 4. Licence | Issue the licence file | You | Your PC |
| 5. Activate | Second run of the installer | Hospital IT | Client's server |
| 6. Later | Updates and renewals | Both | |

**You never copy code to the client.** Everything is built on your PC and only the finished bundle goes to the hospital. The bundle holds the compiled program (no source code) and the installers. The client's server needs only **Docker** — no Python, Node or source code — and can run **Linux** or **Windows**.

**One bundle serves every client.** What differs per hospital is only its licence file: features, number of users, expiry date, and the machine it's bound to.

**Never send:** the `Backend` or `Frontend` folders, `venv`, any `.env` file, or your private signing key (`license_signing_key.pem`).

## Part 1: Build the bundle

On your PC, with **Docker Desktop running**, open **WSL (Ubuntu)**:

```
# WSL (Ubuntu) on your PC
cd /mnt/e/Aniket/next/Hospital/Hospital/Backend
make bundle VERSION=1.0.1
```

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

On **your PC**, in **Command Prompt**:

```
REM Command Prompt on your PC
cd /d E:\Aniket\next\Hospital\Hospital\Backend\tools
set LICENSE_SIGNING_KEY_PATH=C:\Users\User\Desktop\key\license_signing_key.pem
..\venv\Scripts\python.exe -m license_issuer new
```

| Question | What to type |
|---|---|
| Customer / hospital legal name | The hospital's legal name, as in the contract |
| Deployment ID | **Paste from their REQUEST-LICENCE.txt.** Never leave it blank. |
| Start and expiry date | From the contract (YYYY-MM-DD), or Enter for today / one year |
| Grace period | Enter for 14 days, or as agreed |
| Maximum active users | From the contract |
| Features | The numbers of the purchased features, e.g. `1,3,4`, then Enter on the empty line |
| Bind to one machine? | `y`, then **paste the fingerprint from REQUEST-LICENCE.txt** |
| Output file | e.g. `license-city-hospital.lic` |

Check it before sending:

```
REM Command Prompt on your PC
..\venv\Scripts\python.exe -m license_issuer verify license-city-hospital.lic
```

It must say **Signature OK**, and its Deployment ID and fingerprint must match `REQUEST-LICENCE.txt`. Then **send the `.lic` file to the hospital.** It is safe to email: it can't be edited and only works on their server.

> You can also issue licences from the SaaS console: Tenants & Subscriptions → the hospital → **License**. It produces the same kind of file.

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

## Part 6: Updates and renewals

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

**Licence renewal** (yearly, or when they buy more users or features):

```
REM Command Prompt on your PC
..\venv\Scripts\python.exe -m license_issuer renew license-city-hospital.lic
```

Send the new file. The hospital admin uploads it in **Settings → License** — it applies immediately, no restart.

**Full reference:** `deploy/RUNBOOK.md` (troubleshooting, HTTPS, rollback). **Practice run:** `docs/TRIAL_INSTALL_WINDOWS.pdf`.
