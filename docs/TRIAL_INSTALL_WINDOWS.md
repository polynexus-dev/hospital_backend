# Trial install on a Windows PC

| Part | What you do | Where |
|---|---|---|
| 1. Clean up | Remove the earlier attempt | PowerShell (admin) |
| 2. Unpack | Copy the bundle into C:\hms-test | PowerShell (admin) |
| 3. First run | Get the deployment ID and fingerprint | PowerShell (admin) |
| 4. Issue licence | Create the licence in the SaaS console | Command Prompt + browser |
| 5. Check licence | Make sure the IDs match | Command Prompt |
| 6. Finish install | Activate and create the admin | PowerShell (admin) |
| 7. Test | Log in and check the licence | Browser |

This installs the hospital system on this PC exactly as a hospital would, and you play both roles: the **hospital** (running the installer) and **Polynexus** (issuing the licence).

You'll use three windows:

- **Window A — PowerShell as administrator** (Start → type *PowerShell* → right-click → *Run as administrator*). Used for the installer.
- **Window B and C — Command Prompt** (Start → type *cmd*). Used to run your SaaS console and issue the licence.

**Before you start:** Docker Desktop must be running (whale icon in the taskbar, status *Running*).

## Part 1: Clean up the earlier attempt

In **Window A**:

```
# Window A - PowerShell (administrator)
cd C:\
docker rm -f $(docker ps -aq --filter label=com.docker.compose.project=hms)
docker volume rm $(docker volume ls -q --filter label=com.docker.compose.project=hms)
Remove-Item -Recurse -Force C:\hms-test
```

If a line says *"requires at least 1 argument"* or *"Cannot find path"*, there was nothing left to remove: that's fine. If it says the folder *"is being used by another process"*, close any other window that is inside `C:\hms-test` (for example a Command Prompt) and run the last line again.

> These lines delete the test containers and the test database. Only ever do this on a test install, never on a hospital's server.

## Part 2: Unpack the bundle

In **Window A**:

```
# Window A - PowerShell (administrator)
mkdir -Force C:\hms-test
cd E:\Aniket\next\Hospital\Hospital\Backend\dist
tar -xzf bundle-1.0.0.tar.gz -C C:\hms-test --strip-components=1
cd C:\hms-test
dir
```

> Type or paste each command as **one line**. If a command ever looks split over two lines, join it before pressing Enter.

You should see `images.tar`, `install.ps1`, `docker-compose.yml`, `RUNBOOK.md` and a few other files.

## Part 3: First run of the installer

In **Window A**:

```
# Window A - PowerShell (administrator)
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

1. When asked for the hostname, type **localhost** and press Enter.
2. Wait 1–3 minutes while it loads the images and starts the database.
3. It prints two lines, a **Deployment ID** and a **Machine fingerprint**, and saves them to a file.
4. At *"Path to your licence file"*, just **press Enter**. The installer stops; that's expected.

Open the saved file; you'll copy from it in Part 4:

```
# Window A - PowerShell (administrator)
notepad C:\hms-test\config\REQUEST-LICENCE.txt
```

## Part 4: Issue the licence

Licences are issued only in the **SaaS console**, so you start it on this PC with the signing key. This is your own console, in SaaS mode, not the test install.

**One-time:** your SaaS console account needs two-factor authentication. Sign in to the console (step 3 below), open **Settings → Two-factor authentication**, scan the QR code with an authenticator app (Google Authenticator, Microsoft Authenticator) and confirm a code.

1. In **Window B** (Command Prompt), start the backend with the key:

```
REM Window B - Command Prompt
cd /d E:\Aniket\next\Hospital\Hospital\Backend
set LICENSE_SIGNING_KEY_PATH=C:\Users\User\Desktop\key\license_signing_key.pem
venv\Scripts\python.exe manage.py runserver 8000
```

2. Open **Window C** (another Command Prompt) and start the console:

```
REM Window C - Command Prompt
cd /d E:\Aniket\next\Hospital\Hospital\Frontend
npm run dev
```

3. Open **http://localhost:3000/**, sign in as the SaaS Owner, go to **Tenants & Subscriptions**, pick any test hospital (for example *Demo Hospital*) and press **📜 License**.
4. Fill in the form:

| Field | What to enter |
|---|---|
| Deployment ID | **Copy the Deployment ID from Notepad and paste it.** Never leave this blank. |
| Bind to one machine | Ticked. **Copy the Machine fingerprint from Notepad and paste it.** |
| Expires on | Leave (one year) |
| Max active users | `20` |
| Licensed features | Tick **HMS core**, **ERP** and **AI assist** only |
| 2FA code | The current 6-digit code from your authenticator app |

5. Press **Request / Generate License**. As a SaaS Owner, it's signed at once and `LIC-….lic` downloads.
6. Copy it into the test install, in **Window A**:

```
# Window A - PowerShell (administrator)
Copy-Item $HOME\Downloads\LIC-*.lic C:\hms-test\config\license.lic
```

(If there's more than one `LIC-*.lic` in Downloads, copy the newest one by its full name.)

> CRM is deliberately left out, so you can check in Part 7 that its menus are hidden.

## Part 5: Check the licence

In **Window C** (stop the console with Ctrl+C first, or open another Command Prompt):

```
REM Command Prompt
cd /d E:\Aniket\next\Hospital\Hospital\Backend\tools
..\venv\Scripts\python.exe -m license_issuer verify C:\hms-test\config\license.lic
```

It must say **Signature OK** and show **Issued by: PNX-…** (your staff code). Compare two lines with Notepad:

- **Deployment ID** in the output = Deployment ID in Notepad (all characters).
- **Hardware binding: yes (…)** = Machine fingerprint in Notepad.

If either differs, go back to Part 4 and issue it again; the new file replaces the old one.

> Once you've finished testing, move the key off the Desktop: it belongs only on the SaaS server, with one offline backup in a vault.

## Part 6: Finish the install

Back in **Window A**:

```
# Window A - PowerShell (administrator)
cd C:\hms-test
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

This time it finds the licence by itself (no path question):

1. It prepares the database (1–2 minutes).
2. **Administrator email:** type your email, e.g. `admin@demo-hospital.local`.
3. **Owner password:** type a strong password (nothing shows while typing; that's normal).
4. It activates the licence, creates the hospital, and starts everything.

It ends with **Done. Open http://localhost/**.

> If port 80 is already used on this PC (often by IIS), the installer says so and picks another port, such as 8080. Use the address it prints at the end, for example `http://localhost:8080/`.

## Part 7: Test in the browser

1. Open the address the installer printed (**http://localhost/**, or **http://localhost:8080/** if it switched ports) and sign in with the email and password from Part 6.
2. Go to **Settings**, then scroll to the **License** card. Check:
   - Status **Active**
   - Licensed features: **HMS core, ERP, AI assist**
   - User limit **20**, expiry about one year from today
   - Hardware binding: **Bound to this server**
3. Look at the side menu: **Enquiries, Call console and Callbacks (CRM) are not there**, because CRM isn't licensed.
4. Optional, in Window A: `docker compose ps`. All six services should say *running* or *healthy*.

The trial is complete. Next, repeat it on a Linux server with `install.sh` (see `RUNBOOK.md`).

## If something goes wrong

| Message | What to do |
|---|---|
| *This license is for deployment X, but this installation is Y* | The Deployment ID was wrong or left blank. Redo Part 4, pasting it from Notepad, then rerun Part 6. |
| *This license was issued for a different server* | The fingerprint was wrong. Redo Part 4 with the fingerprint from Notepad. |
| *Docker is installed but not running* | Start Docker Desktop, wait until it says Running, try again. |
| *Docker must run Linux containers* | Right-click the Docker whale icon → *Switch to Linux containers*. |
| *No signing key* (Part 4) | The `set LICENSE_SIGNING_KEY_PATH=…` line wasn't run in Window B before `runserver`. Stop it (Ctrl+C), run both lines again. |
| *Enter the current 6-digit code* / *Turn on two-factor* (Part 4) | Set up 2FA (Part 4, one-time), and type the code showing right now in the app. |
| *Only SaaS Owners … can issue licences* (Part 4) | Sign in with a SaaS Owner account. |
| Anything else | Copy the last 20 lines of the window and send them for help. |
