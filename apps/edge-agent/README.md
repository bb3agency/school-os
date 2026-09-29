# SchoolOS Tally edge agent

**Status: ADR-0032 Proposed. Built behind the per-school flag `tally.connector.enabled` (off).
Do not install at a school until the ADR is accepted.**

A small Windows service on the office PC that runs TallyPrime. It reads the ledger groups the
school's accountant selected in SchoolOS and sends each party ledger's closing balance to
SchoolOS. It talks to Tally only on this PC (`127.0.0.1`, XML over HTTP, export requests only)
and to SchoolOS only over outbound HTTPS, signing every request with its device key.

| Topic | Where |
|---|---|
| Decision, trust boundary, signing, data sent | `docs/adr/ADR-0032-tally-edge-agent.md` |
| Server side (routes, tables, `get_fee_dues`) | `apps/api/app/tally/`, docs/09 §4 "Tally connector" |
| Operations (install, updates, firewall) | docs/10 §13 |

## Install (per school PC)

1. In TallyPrime: Help > Settings > Connectivity: "TallyPrime acts as Server", port 9000. In
   Windows Defender Firewall, block inbound 9000 from other machines.
2. Create a local Windows account for the service (e.g. `SchoolOSAgent`), without admin rights.
3. Install the signed package (Python runtime + this package + WinSW) into
   `C:\Program Files\SchoolOS\TallyAgent`.
4. The school owner creates a one-time code on SchoolOS > Tally connector (it needs a recent MFA
   sign-in and is valid for 30 minutes).
5. As the service account: `sos-tally-agent enrol --server https://<school address> --school
   <school id> --code XXXX-XXXX-XXXX`. The device key is protected with Windows DPAPI for that
   account only.
6. `sos-tally-agent.exe install` and `start` (WinSW, `windows/sos-tally-agent.xml`).
7. The accountant chooses the ledger groups on the Tally connector screen, then links ledgers to
   students.

`sos-tally-agent check-tally` checks Tally's XML server; `status` shows the configuration (never
the key); `rotate-key` gets a new key (also done automatically every 90 days).

## What is stored on the PC

- `%LOCALAPPDATA%\SchoolOS\TallyAgent\agent.toml` (service account): SchoolOS address, school
  id, device id, key id, Tally address. No secrets.
- `device.key`: the device key, DPAPI-protected (user scope, school id as entropy).
- `agent.log` (rotated, 5 x 1 MB): event names, counts and error codes only. Never ledger names,
  amounts, the code or the key.

Snapshots are kept in memory for one attempt; when SchoolOS is unreachable the agent retries
with backoff (1 minute doubling to 30 minutes) and takes a fresh snapshot.

## Development

```bash
uv run pytest apps/edge-agent/tests -q      # synthetic Tally XML fixtures, fake SchoolOS
uv run mypy apps/edge-agent
uv run sos-tally-agent --insecure-file-store --state-dir /tmp/agent status
```

Outside Windows the key can only be kept with `--insecure-file-store` (development only).
