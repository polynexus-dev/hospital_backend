<#
Polynexus HMS - upgrade an existing Windows installation. Works offline.

  Run as administrator, from the NEW unpacked bundle:
  powershell -ExecutionPolicy Bypass -File .\upgrade.ps1 [-InstallDir C:\hms]

Order: database backup, load new images, update deploy files (your .env,
licence and data are kept), migrate, restart.
#>
param([string]$InstallDir = "C:\hms")
$ErrorActionPreference = "Stop"
$Bundle = $PSScriptRoot

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Die($msg) { Write-Host "Error: $msg" -ForegroundColor Red; exit 1 }
function Compose { docker compose -f docker-compose.yml @args; if ($LASTEXITCODE -ne 0) { Die "docker compose $args failed." } }

if (-not (Test-Path (Join-Path $InstallDir ".env"))) { Die "$InstallDir has no .env: is that where HMS is installed? Use -InstallDir." }
$NewVersion = (Get-Content (Join-Path $Bundle "VERSION") -Raw).Trim()
Set-Location $InstallDir
$envVars = @{}
Get-Content .env | Where-Object { $_ -match "^\s*([A-Z_]+)=(.*)$" } | ForEach-Object { $envVars[$Matches[1]] = $Matches[2] }
$OldVersion = $envVars["HMS_VERSION"]
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

# --- 1. Backup first ------------------------------------------------------------
New-Item -ItemType Directory -Force -Path backups | Out-Null
Say "Backing up the database (version $OldVersion)"
Compose up -d postgres
do { Start-Sleep -Seconds 2; docker compose -f docker-compose.yml exec -T postgres pg_isready -U $envVars["POSTGRES_USER"] *> $null } while ($LASTEXITCODE -ne 0)
Compose exec -T postgres pg_dump -U $envVars["POSTGRES_USER"] -d $envVars["POSTGRES_DB"] -Fc -f /tmp/hms-backup.dump
Compose cp postgres:/tmp/hms-backup.dump "backups/hms-$OldVersion-$stamp.dump"
Copy-Item .env "backups\env-$stamp"
if (Test-Path config\license.lic) { Copy-Item config\license.lic "backups\license-$stamp.lic" }
Write-Host "Backup: $InstallDir\backups\hms-$OldVersion-$stamp.dump"

# --- 2. New images and deploy files --------------------------------------------
Say "Loading images for $NewVersion"
docker load -i (Join-Path $Bundle "images.tar"); if ($LASTEXITCODE -ne 0) { Die "docker load failed." }
if ((Resolve-Path $Bundle).Path -ne (Resolve-Path $InstallDir).Path) {
    foreach ($f in "docker-compose.yml", "env.template", "install.sh", "install.ps1", "upgrade.sh", "upgrade.ps1", "RUNBOOK.md", "README.md", "VERSION") {
        if (Test-Path (Join-Path $Bundle $f)) { Copy-Item (Join-Path $Bundle $f) $InstallDir -Force }
    }
}
$text = (Get-Content .env -Raw) -replace "(?m)^HMS_VERSION=.*$", "HMS_VERSION=$NewVersion"
[System.IO.File]::WriteAllText((Join-Path $InstallDir ".env"), ($text -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding $false))

# Refresh the machine identity used by the licence fingerprint.
$machineGuid = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Cryptography" -Name MachineGuid).MachineGuid
$biosUuid = (Get-CimInstance Win32_ComputerSystemProduct).UUID
New-Item -ItemType Directory -Force -Path config\host-identity | Out-Null
[System.IO.File]::WriteAllText((Join-Path $InstallDir "config\host-identity\machine-id"), ($machineGuid.Replace("-", "").ToLower() + "`n"), (New-Object System.Text.UTF8Encoding $false))
[System.IO.File]::WriteAllText((Join-Path $InstallDir "config\host-identity\product_uuid"), (("$biosUuid").ToLower() + "`n"), (New-Object System.Text.UTF8Encoding $false))

# --- 3. Migrate and restart ------------------------------------------------------
Say "Migrating the database"
Compose run --rm -e RUN_MIGRATIONS=0 web python manage.py migrate --noinput
Say "Restarting services"
Compose up -d --remove-orphans
Write-Host "`nUpgraded $OldVersion -> $NewVersion."
Write-Host "To roll back: set HMS_VERSION=$OldVersion in .env, restore backups\hms-$OldVersion-$stamp.dump (see RUNBOOK.md), then: docker compose up -d"
