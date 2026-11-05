# SchoolOS Tally edge agent

**Status: ADR-0032 Proposed. Built behind the per-school flag `tally.connector.enabled` (off).
Do not install at a school until the ADR is accepted.**

A small Windows service on the office PC that runs TallyPrime. It reads the ledger groups the
school's accountant selected in SchoolOS and sends each party ledger's closing balance to
SchoolOS, with the ledger's pending bills (bill-wise allocations: bill name, bill date, due
date, pending amount) when Tally keeps them (D63; bills of other ledgers never leave the PC). It
talks to Tally only on this PC (`127.0.0.1`, XML over HTTP, export requests only) and to
SchoolOS only over outbound HTTPS, signing every request with its device key. Every request
carries `User-Agent: SchoolOS-Edge-Agent/<version>` (urllib's default agent is blocked by
common firewall rule sets).

| Topic | Where |
|---|---|
| Decision, trust boundary, signing, data sent | `docs/adr/ADR-0032-tally-edge-agent.md` |
| Server side (routes, tables, `get_fee_dues`) | `apps/api/app/tally/`, docs/09 §4 "Tally connector" |
| Operations (install, updates, firewall) | docs/10 §13 |

## Install (per school PC)

> **Unsigned builds are not for schools.** A release built without the code-signing secrets is
> named `schoolos-tally-agent-<version>-x64-unsigned.msi`. Use it only on test PCs. Install only
> an MSI whose Authenticode signature shows SchoolOS (right-click > Properties > Digital
> Signatures) and whose SHA-256 matches the `.sha256` file from the same release.

1. In TallyPrime: Help > Settings > Connectivity: "TallyPrime acts as Server", port 9000. In
   Windows Defender Firewall, block inbound 9000 from other machines.
2. As an administrator, run `schoolos-tally-agent-<version>-x64.msi` (per machine, 64-bit Windows
   10/11). It installs into `C:\Program Files\SchoolOS\TallyAgent` (Python runtime, this package,
   WinSW) and registers and starts the service **SchoolOS Tally agent** (`SchoolOSTallyAgent`):
   virtual account `NT SERVICE\SchoolOSTallyAgent` (no password, nobody can sign in as it),
   automatic (delayed) start, restart on failure. No account needs creating. Until it is
   enrolled the service just waits.
3. The school owner creates a one-time code on SchoolOS > Tally connector (it needs a recent MFA
   sign-in and is valid for 30 minutes).
4. From an **elevated** Command Prompt:

   ```bat
   "C:\Program Files\SchoolOS\TallyAgent\tally-agent.cmd" enrol --via-service --server https://<school address> --school <school id> --code XXXX-XXXX-XXXX
   ```

   This leaves the request in the state folder; the service enrols itself within a minute (so
   the device key is protected with Windows DPAPI for the service account only) and deletes the
   request whether or not the code worked. Check with `tally-agent.cmd status`; if it still says
   not enrolled, `agent.log` in the state folder has the error code (create a new code).
5. The accountant chooses the ledger groups on the Tally connector screen, then links ledgers to
   students.

`tally-agent.cmd check-tally` checks Tally's XML server; `status` shows the configuration (never
the key). The service syncs every 30 minutes and rotates its key every 90 days (`sync-once` and
`rotate-key` need the device key, so they work only inside the service).

## Upgrade

No auto-update. When SchoolOS says the agent is outdated (`min_agent_version`), run the new MSI as
an administrator: it stops the service, replaces the program files, and starts it again.
Enrolment, configuration and the key in `ProgramData` are kept. Installing an older MSI over a
newer one is refused.

## Uninstall

Settings > Apps > SchoolOS Tally agent > Uninstall (or `msiexec /x` with the MSI). This stops and
removes the service and the program files, and **leaves** `C:\ProgramData\SchoolOS\TallyAgent`
(configuration, device key, logs) so a reinstall keeps working. To remove the PC completely:

1. Revoke the device on SchoolOS > Tally connector (the key stops working at once).
2. From an elevated prompt: `rmdir /s /q "C:\ProgramData\SchoolOS\TallyAgent"` (and
   `C:\ProgramData\SchoolOS` if it is empty).

## What is stored on the PC

`C:\ProgramData\SchoolOS\TallyAgent` (MSI install; ACL: the service account and Administrators
only, nothing inherited; `%LOCALAPPDATA%\SchoolOS\TallyAgent` of the account for a manual run):

- `agent.toml`: SchoolOS address, school id, device id, key id, Tally address. No secrets.
- `device.key`: the device key, DPAPI-protected (user scope, school id as entropy).
- `enrol-request.toml`: only between `enrol --via-service` and the service's next check (it holds
  the one-time code; deleted after one attempt).
- `agent.log` (rotated, 5 x 1 MB): event names, counts and error codes only. Never ledger names,
  amounts, the code or the key. WinSW's own logs are in `logs\`.

Snapshots are kept in memory for one attempt; when SchoolOS is unreachable the agent retries
with backoff (1 minute doubling to 30 minutes) and takes a fresh snapshot.

## Building the installer

`installer/` (D69): `SchoolOSTallyAgent.wxs` (WiX Toolset v4), `pins.toml` (exact versions and
SHA-256 of the Python embeddable zip and WinSW, and the WiX version), `build.ps1` (Windows
PowerShell 5.1; needs uv and the `wix` dotnet tool with `WixToolset.Util.wixext`):

```powershell
dotnet tool install --global wix --version 4.0.5
wix extension add --global WixToolset.Util.wixext/4.0.5
powershell -NoProfile -ExecutionPolicy Bypass -File apps\edge-agent\installer\build.ps1 -Version 2026.11.1
```

The release workflow (job `edge-agent-msi`) does this on every CalVer tag and signs the MSI when
the signing secrets exist. The build stops if a pin is a placeholder or a download's SHA-256
differs. `-StageOnly` stops before WiX.

## Development

```bash
uv run pytest apps/edge-agent/tests -q      # synthetic Tally XML fixtures, fake SchoolOS
uv run mypy apps/edge-agent
uv run sos-tally-agent --insecure-file-store --state-dir /tmp/agent status
```

Outside Windows the key can only be kept with `--insecure-file-store` (development only).
