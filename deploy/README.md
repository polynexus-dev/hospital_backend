# Polynexus HMS — installation bundle

Everything needed to run Polynexus HMS on a hospital's own server, with no
internet access.

- **Linux:** `sudo ./install.sh`
- **Windows** (Docker Desktop, Linux containers): in an administrator PowerShell,
  `powershell -ExecutionPolicy Bypass -File .\install.ps1`

The first run prints this server's **deployment ID** and **machine
fingerprint** (also saved in `config/REQUEST-LICENCE.txt`): send them to
Polynexus. Run the installer again with the licence file you receive.

Upgrades: unpack the new bundle in a separate folder and run `upgrade.sh` /
`upgrade.ps1` from it. Full instructions and troubleshooting: `RUNBOOK.md`.

Support: support@polynexus.in
