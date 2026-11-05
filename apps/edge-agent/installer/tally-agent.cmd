@echo off
rem SchoolOS Tally agent command line for administrators (D69). Run from an elevated prompt:
rem only the service account and Administrators can open the state folder.
rem   tally-agent.cmd enrol --via-service --server https://<school>.schoolos.in --school <id> --code XXXX-XXXX-XXXX
rem   tally-agent.cmd status
rem   tally-agent.cmd check-tally
rem The device key belongs to the service account: sync-once and rotate-key only work in the
rem service itself (it syncs every 30 minutes and rotates the key every 90 days).
"%~dp0python\python.exe" -I -m sos_edge_agent.cli --state-dir "%ProgramData%\SchoolOS\TallyAgent" %*
