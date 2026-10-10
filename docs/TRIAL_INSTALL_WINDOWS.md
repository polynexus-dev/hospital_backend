# Trial install on a Windows PC

| Part | What you do | Where |
|---|---|---|
| 1. Clean up | Remove the earlier attempt | PowerShell (admin) |
| 2. Unpack | Copy the bundle into C:\hms-test | PowerShell (admin) |
| 3. First run | Get the deployment ID and fingerprint | PowerShell (admin) |
| 4. Issue licence | Create the licence file | Command Prompt |
| 5. Check licence | Make sure the IDs match | Command Prompt |
| 6. Finish install | Activate and create the admin | PowerShell (admin) |
| 7. Test | Log in and check the licence | Browser |

This installs the hospital system on this PC exactly as a hospital would, and you play both roles: the **hospital** (running the installer) and **Polynexus** (issuing the licence).

You'll use two windows:

- **Window A — PowerShell as administrator** (Start → type *PowerShell* → right-click → *Run as administrator*). Used for the installer.
- **Window B — Command Prompt** (Start → type *cmd*). Used to issue the licence.

**Before you start:** Docker Desktop must be running (whale icon in the taskbar, status *Running*).

## Part 1: Clean up the earlier attempt

In **Window A**:

```
cd C:\hms-test
docker compose down -v
cd C:\
Remove-Item -Recurse -Force C:\hms-test
```

If the first two lines say there is no such folder or configuration file, that's fine: carry on with the last line.

> `down -v` deletes the test database. Only ever do this on a test install, never on a hospital's server.

## Part 2: Unpack the bundle

In **Window A**:

```
mkdir C:\hms-test
tar -xzf E:\Aniket\next\Hospital\Hospital\Backend\dist\bundle-1.0.0.tar.gz -C C:\hms-test --strip-components=1
cd C:\hms-test
dir
```

You should see `images.tar`, `install.ps1`, `docker-compose.yml`, `RUNBOOK.md` and a few other files.

## Part 3: First run of the installer

In **Window A**:

```
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

1. When asked for the hostname, type **localhost** and press Enter.
2. Wait 1–3 minutes while it loads the images and starts the database.
3. It prints two lines, a **Deployment ID** and a **Machine fingerprint**, and saves them to a file.
4. At *"Path to your licence file"*, just **press Enter**. The installer stops; that's expected.

Open the saved file; you'll copy from it in Part 4:

```
notepad C:\hms-test\config\REQUEST-LICENCE.txt
```

## Part 4: Issue the licence

In **Window B** (Command Prompt):

```
cd E:\Aniket\next\Hospital\Hospital\Backend\tools
set LICENSE_SIGNING_KEY_PATH=C:\Users\User\Desktop\key\license_signing_key.pem
..\venv\Scripts\python.exe -m license_issuer new
```

Answer the questions:

| Question | What to type |
|---|---|
| Customer / hospital legal name | `Demo Hospital` |
| Deployment ID | **Copy the Deployment ID from Notepad and paste it.** Never leave this blank. |
| Licence start date | press Enter (today) |
| Licence expiry date | press Enter (one year) |
| Grace period in days | press Enter (14) |
| Maximum active users | `20` |
| Toggle (features list) | `1,3,7` then Enter. The list redraws with HMS core, ERP and AI assist ticked. Press **Enter again on the empty line** to finish. |
| Bind to one machine? | `y` |
| Machine fingerprint | **Copy the Machine fingerprint from Notepad and paste it.** |
| Output file | `C:\hms-test\config\license.lic` |

It prints a summary and *Wrote C:\hms-test\config\license.lic*.

> CRM (option 2) is deliberately left out, so you can check in Part 7 that its menus are hidden.

## Part 5: Check the licence

Still in **Window B**:

```
..\venv\Scripts\python.exe -m license_issuer verify C:\hms-test\config\license.lic
```

It must say **Signature OK**. Compare two lines with Notepad:

- **Deployment ID** in the output = Deployment ID in Notepad (all characters).
- **Hardware binding: yes (…)** = Machine fingerprint in Notepad.

If either differs, go back to Part 4 and issue it again; the new file replaces the old one.

## Part 6: Finish the install

Back in **Window A**:

```
cd C:\hms-test
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

This time it finds the licence by itself (no path question):

1. It prepares the database (1–2 minutes).
2. **Administrator email:** type your email, e.g. `admin@demo-hospital.local`.
3. **Owner password:** type a strong password (nothing shows while typing; that's normal).
4. It activates the licence, creates the hospital, and starts everything.

It ends with **Done. Open http://localhost/**.

> If it fails at the end with a message about port 80 being in use, another program (often IIS) has that port. Open `C:\hms-test\.env` in Notepad, change `HTTP_PORT=80` to `HTTP_PORT=8080`, save, run `docker compose up -d` in Window A, and use `http://localhost:8080/` in Part 7.

## Part 7: Test in the browser

1. Open **http://localhost/** and sign in with the email and password from Part 6.
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
| *No signing key* (Part 4) | The `set LICENSE_SIGNING_KEY_PATH=…` line wasn't run in this window. Run it again. |
| Anything else | Copy the last 20 lines of the window and send them for help. |
