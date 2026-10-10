<#
Polynexus HMS - put the installation on a domain (e.g. hms.hospital.com),
with HTTPS. Run from the install folder on a Windows server:

  powershell -ExecutionPolicy Bypass -File .\configure-domain.ps1   (PowerShell as administrator)

Before running: the domain's DNS record must point at this server
(internal DNS for in-hospital use, public DNS for internet access).
#>
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Die($msg) { Write-Host "Error: $msg" -ForegroundColor Red; exit 1 }
function Compose { docker compose -f docker-compose.yml @args; if ($LASTEXITCODE -ne 0) { Die "docker compose $args failed." } }
function Write-Utf8NoBom($path, $text) { [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot $path), $text, (New-Object System.Text.UTF8Encoding $false)) }
function Set-EnvValue($name, $value) {
    $text = Get-Content .env -Raw
    if ($text -match "(?m)^$name=") { $text = $text -replace "(?m)^$name=.*$", "$name=$value" }
    else { $text = $text.TrimEnd("`r", "`n") + "`n$name=$value`n" }
    Write-Utf8NoBom ".env" ($text -replace "`r`n", "`n")
}
function Read-EnvVars {
    $vars = @{}
    Get-Content .env | Where-Object { $_ -match "^\s*([A-Z_]+)=(.*)$" } | ForEach-Object { $vars[$Matches[1]] = $Matches[2] }
    return $vars
}

if (-not (Test-Path .env)) { Die "Run this from the install folder (no .env here). Install first with install.ps1." }
$envVars = Read-EnvVars

$domain = Read-Host "Domain staff will use (e.g. hms.hospital.com)"
if ($domain -notmatch "^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$") { Die "That doesn't look like a domain name." }
try { [void][System.Net.Dns]::GetHostAddresses($domain) } catch { Write-Host "Warning: $domain doesn't resolve from this server yet. Add the DNS record first if it's missing." -ForegroundColor Yellow }

Write-Host ""
Write-Host "How should HTTPS be set up?"
Write-Host "  1) The hospital's own certificate (PEM files you already have)"
Write-Host "  2) Let's Encrypt - free, renews automatically (server must be reachable from the internet on port 80)"
Write-Host "  3) No certificate for now - plain HTTP (internal network only)"
$mode = Read-Host "Choose 1, 2 or 3"

Say "Pointing the installation at $domain"
Set-EnvValue "ALLOWED_HOSTS" "$domain,localhost,web"
Set-EnvValue "CORS_ALLOWED_ORIGINS" "https://$domain,http://$domain"
Set-EnvValue "CSRF_TRUSTED_ORIGINS" "https://$domain,http://$domain"
New-Item -ItemType Directory -Force -Path certs | Out-Null

switch ($mode) {
    "1" {
        $crt = Read-Host "Path to the certificate file (PEM, including the intermediate chain)"
        $key = Read-Host "Path to the private key file (PEM)"
        if (-not (Test-Path $crt) -or -not (Test-Path $key)) { Die "Certificate or key file not found." }
        Copy-Item $crt certs\tls.crt.new -Force
        Copy-Item $key certs\tls.key.new -Force
        Say "Checking the certificate with nginx"
        # nginx already sees certs\ (read-only); test the HTTPS config against the .new files.
        docker compose -f docker-compose.yml run --rm --no-deps nginx sh -c "sed -e 's#certs/tls.crt#certs/tls.crt.new#' -e 's#certs/tls.key#certs/tls.key.new#' /etc/nginx/hms/https.conf > /etc/nginx/conf.d/hms.conf && nginx -t"
        if ($LASTEXITCODE -ne 0) {
            Remove-Item certs\tls.crt.new, certs\tls.key.new -Force
            Die "nginx rejected the certificate/key (wrong pair, or not PEM - a .pfx must be converted first). Nothing was changed."
        }
        Move-Item certs\tls.crt.new certs\tls.crt -Force
        Move-Item certs\tls.key.new certs\tls.key -Force
        Set-EnvValue "SECURE_SSL_REDIRECT" "True"
    }
    "2" {
        $httpPort = if ($envVars["HTTP_PORT"]) { $envVars["HTTP_PORT"] } else { "80" }
        if ($httpPort -ne "80") { Die "Let's Encrypt needs HTTP on port 80, but HTTP_PORT=$httpPort. Free port 80, set HTTP_PORT=80 in .env, and run this again." }
        $email = Read-Host "Email for Let's Encrypt expiry notices"
        if (-not $email) { Die "An email is required." }
        Compose up -d nginx
        Say "Requesting a certificate for $domain (needs internet)"
        docker compose -f docker-compose.yml --profile letsencrypt run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/acme -d $domain --email $email --agree-tos --no-eff-email -n
        if ($LASTEXITCODE -ne 0) { Die "Let's Encrypt couldn't verify $domain. Check that its public DNS points at this server and port 80 is open to the internet." }
        Compose --profile letsencrypt run --rm --entrypoint sh certbot -c "cp /etc/letsencrypt/live/$domain/fullchain.pem /certs/tls.crt && cp /etc/letsencrypt/live/$domain/privkey.pem /certs/tls.key"
        Set-EnvValue "COMPOSE_PROFILES" "letsencrypt"   # keeps the renewal service running from now on
        Set-EnvValue "SECURE_SSL_REDIRECT" "True"
    }
    "3" { Set-EnvValue "SECURE_SSL_REDIRECT" "False" }
    default { Die "Choose 1, 2 or 3." }
}

Say "Restarting"
Compose up -d
Compose up -d --force-recreate web worker beat nginx

$envVars = Read-EnvVars
if ($mode -eq "3") {
    $url = "http://$domain"
    if ($envVars["HTTP_PORT"] -and $envVars["HTTP_PORT"] -ne "80") { $url += ":" + $envVars["HTTP_PORT"] }
} else {
    $url = "https://$domain"
    if ($envVars["HTTPS_PORT"] -and $envVars["HTTPS_PORT"] -ne "443") { $url += ":" + $envVars["HTTPS_PORT"] }
}
Write-Host "`nDone. Staff can now open $url/"
if ($mode -eq "2") { Write-Host "The certificate renews automatically (checked twice a day)." }
if ($mode -eq "1") { Write-Host "When the certificate is replaced, run this again (or overwrite certs\tls.crt and tls.key: picked up within an hour)." }
