<#
Polynexus HMS - first-time install on a Windows server. Works with no
internet access: images come from images.tar in this bundle.

Requires Docker running Linux containers: Docker Desktop (Windows 10/11 and
Windows Server with WSL2) or Docker Engine inside WSL2.

  Right-click PowerShell > Run as administrator, then in the unpacked bundle folder:
  powershell -ExecutionPolicy Bypass -File .\install.ps1

Safe to re-run: existing .env, data and licence are kept. The first run
prints the deployment ID and fingerprint to send to Polynexus; run it again
with the licence file to finish.
#>
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Die($msg) { Write-Host "Error: $msg" -ForegroundColor Red; exit 1 }
function Compose { docker compose -f docker-compose.yml @args; if ($LASTEXITCODE -ne 0) { Die "docker compose $args failed." } }
function Write-Utf8NoBom($path, $text) { [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot $path), $text, (New-Object System.Text.UTF8Encoding $false)) }
function Random-Bytes($n) { $b = New-Object byte[] $n; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); return ,$b }
function Hex($n) { -join ((Random-Bytes $n) | ForEach-Object { $_.ToString("x2") }) }
function B64($n) { [Convert]::ToBase64String((Random-Bytes $n)) }
function Test-PortFree($port) {
    try { $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, [int]$port); $l.Start(); $l.Stop(); return $true } catch { return $false }
}
function Set-EnvValue($name, $value) {
    $text = (Get-Content .env -Raw) -replace "(?m)^$name=.*$", "$name=$value"
    Write-Utf8NoBom ".env" ($text -replace "`r`n", "`n")
}
# Use the configured port if it's free (or already ours), else the first free fallback.
function Select-Port($name, $fallbacks) {
    $current = $envVars[$name]
    if ((docker compose -f docker-compose.yml ps -q nginx 2>$null) -or (Test-PortFree $current)) { return $current }
    foreach ($p in $fallbacks) {
        if (Test-PortFree $p) {
            Write-Host "Port $current is in use on this PC; using $p instead." -ForegroundColor Yellow
            Set-EnvValue $name $p
            $envVars[$name] = "$p"
            return "$p"
        }
    }
    Die "Ports $current and $($fallbacks -join ', ') are all in use. Set $name in .env to a free port and run this again."
}

# --- 1. Prerequisites ---------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Die "Docker is not installed. Install Docker Desktop (Linux containers)." }
docker compose version *> $null; if ($LASTEXITCODE -ne 0) { Die "Docker Compose v2 is required." }
docker info *> $null; if ($LASTEXITCODE -ne 0) { Die "Docker is installed but not running. Start Docker Desktop and retry." }
$osType = docker info --format "{{.OSType}}"
if ($osType -ne "linux") { Die "Docker must run Linux containers (Docker Desktop: 'Switch to Linux containers')." }
$Version = if (Test-Path VERSION) { (Get-Content VERSION -Raw).Trim() } else { "latest" }

# --- 2. Images (offline) ------------------------------------------------------
if (Test-Path images.tar) {
    Say "Loading images (version $Version)"
    docker load -i images.tar; if ($LASTEXITCODE -ne 0) { Die "docker load failed." }
}

# --- 3. Machine identity (for the licence fingerprint) ------------------------
# Linux containers can't read Windows' identity, so it's written to files the
# containers mount. Refreshed on every run.
New-Item -ItemType Directory -Force -Path config\host-identity, certs, backups | Out-Null
$machineGuid = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Cryptography" -Name MachineGuid).MachineGuid
$biosUuid = (Get-CimInstance Win32_ComputerSystemProduct).UUID
if (-not $machineGuid) { Die "Couldn't read this machine's MachineGuid." }
Write-Utf8NoBom "config\host-identity\machine-id" ($machineGuid.Replace("-", "").ToLower() + "`n")
Write-Utf8NoBom "config\host-identity\product_uuid" (("$biosUuid").ToLower() + "`n")

# --- 4. Configuration and secrets ---------------------------------------------
if (-not (Test-Path .env)) {
    Say "Creating .env with fresh secrets"
    $hostName = Read-Host "Hostname or IP staff will use to reach this server (e.g. hms.hospital.local)"
    if (-not $hostName) { Die "A hostname or IP is required." }
    $envText = (Get-Content env.template -Raw) `
        -replace "__VERSION__", $Version `
        -replace "__DEPLOYMENT_ID__", ([guid]::NewGuid().ToString()) `
        -replace "__HOSTS__", "$hostName,localhost,web" `
        -replace "__ORIGINS__", "http://$hostName,https://$hostName" `
        -replace "__POSTGRES_PASSWORD__", (Hex 24) `
        -replace "__SECRET_KEY__", (Hex 32) `
        -replace "__FERNET_KEY__", ((B64 32).Replace("+", "-").Replace("/", "_")) `
        -replace "__GCM_KEY__", (B64 32) `
        -replace "__HASH_KEY__", (Hex 32) `
        -replace "__HOST_MACHINE_ID__", "./config/host-identity/machine-id" `
        -replace "__HOST_PRODUCT_UUID__", "./config/host-identity/product_uuid"
    Write-Utf8NoBom ".env" ($envText -replace "`r`n", "`n")
    Write-Host "Saved .env. Back it up somewhere safe: its keys decrypt patient data."
}
$envVars = @{}
Get-Content .env | Where-Object { $_ -match "^\s*([A-Z_]+)=(.*)$" } | ForEach-Object { $envVars[$Matches[1]] = $Matches[2] }

Say "Starting the database"
Compose up -d postgres redis

# --- 5. Licence ---------------------------------------------------------------
if (-not (Test-Path config\license.lic) -or (Get-Item config\license.lic).Length -eq 0) {
    Say "Reading this server's machine fingerprint (about 30 seconds)"
    # -T: no terminal. The output is captured here, and a terminal session
    # with captured output can hang docker compose run on Windows.
    $fp = (docker compose -f docker-compose.yml run -T --rm --no-deps -e RUN_MIGRATIONS=0 web python manage.py get_machine_fingerprint | Select-Object -Last 1)
    if (-not $fp -or $fp.Length -ne 64) { Die "Couldn't read the machine fingerprint (got: '$fp'). Run: docker compose run -T --rm web python manage.py get_machine_fingerprint" }
    $request = "Deployment ID: $($envVars['DEPLOYMENT_ID'])`nMachine fingerprint: $fp`n"
    Write-Host $request
    Write-Utf8NoBom "config\REQUEST-LICENCE.txt" $request
    Say "Send the two lines above (saved in config\REQUEST-LICENCE.txt) to Polynexus to get your licence."
    $lic = Read-Host "Path to your licence file (leave empty to stop here and re-run later)"
    if (-not $lic) { Write-Host "Re-run install.ps1 when you have the licence."; exit 0 }
    if (-not (Test-Path $lic)) { Die "No file at $lic" }
    Copy-Item $lic config\license.lic -Force
}

# --- 6. Database, hospital and admin account ---------------------------------
Say "Preparing the database"
Compose run --rm -e RUN_MIGRATIONS=0 web python manage.py migrate --noinput

$adminEmail = Read-Host "Administrator email"
if (-not $adminEmail) { Die "An administrator email is required." }
Say "Activating the licence (you'll be asked for the administrator password)"
Compose run --rm -e RUN_MIGRATIONS=0 web python manage.py setup_onprem --license /app/config/license.lic --admin-email $adminEmail

# --- 7. Start -----------------------------------------------------------------
$httpPort = Select-Port "HTTP_PORT" @(8080, 8081, 8000, 8888)
$httpsPort = Select-Port "HTTPS_PORT" @(8443, 9443)
Say "Starting all services"
Compose up -d
$firstHost = ($envVars["ALLOWED_HOSTS"] -split ",")[0]
$portSuffix = if ($httpPort -eq "80") { "" } else { ":$httpPort" }
Write-Host "`nDone. Open http://localhost$portSuffix/ on this PC, or http://$firstHost$portSuffix/ from the network, and sign in as $adminEmail."
Write-Host "Status: docker compose ps    Logs: docker compose logs -f web"
