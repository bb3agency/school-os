"""``sos-tally-agent``: enrol, run (the Windows service entry point), sync once, status, key
rotation and a Tally check (ADR-0032 §2, §7).

    sos-tally-agent enrol --server https://school.example.in --school <id> --code XXXX-XXXX-XXXX
    sos-tally-agent run            # the Windows service (WinSW, windows/sos-tally-agent.xml)
    sos-tally-agent sync-once
    sos-tally-agent status
    sos-tally-agent rotate-key
    sos-tally-agent check-tally

Run ``enrol`` as the same Windows account the service runs as: the device key is protected with
that account's DPAPI key. Exit codes: 0 ok, 1 failed (the message says why), 2 bad arguments.
Messages and logs never contain ledger names, amounts, the code or the key.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import logging
import logging.handlers
import platform
import signal
import sys
import threading
import uuid
from collections.abc import Sequence
from pathlib import Path
from types import FrameType

from sos_edge_agent import __version__
from sos_edge_agent.client import (
    AgentError,
    SchoolOSClient,
    TallyClient,
    Transport,
    UrllibTransport,
)
from sos_edge_agent.config import (
    CONFIG_NAME,
    AgentConfig,
    ConfigError,
    default_state_dir,
    load,
    save,
)
from sos_edge_agent.credentials import CredentialError, CredentialStore, default_store
from sos_edge_agent.sync import Agent
from sos_edge_agent.tally_xml import (
    TallyXmlError,
    companies_request,
    groups_request,
    parse_companies,
    parse_groups,
)

EXIT_OK = 0
EXIT_FAILED = 1
log = logging.getLogger("sos_edge_agent")


class _SafeFormatter(logging.Formatter):
    """``time level event code=... count=...``: only the fields the agent sets on purpose."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [self.formatTime(record), record.levelname, record.getMessage()]
        for field in ("code", "count"):
            value = getattr(record, field, None)
            if value is not None:
                parts.append(f"{field}={value}")
        return " ".join(parts)


def setup_logging(state_dir: Path, *, verbose: bool = False) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            state_dir / "agent.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        ),
        logging.StreamHandler(sys.stderr),
    ]
    for handler in handlers:
        handler.setFormatter(_SafeFormatter())
    log.handlers[:] = handlers
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False


def _platform() -> str:
    name = f"{platform.system()}-{platform.release()}".lower()
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in name)
    return cleaned[:40] or "unknown"


class Runtime:
    """Everything a command needs, built from the state directory (tests inject a transport)."""

    def __init__(
        self,
        state_dir: Path,
        *,
        transport: Transport | None = None,
        tally_transport: Transport | None = None,
        allow_insecure: bool = False,
        store: CredentialStore | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.config_path = state_dir / CONFIG_NAME
        self.transport = transport or UrllibTransport()
        self.tally_transport = tally_transport or self.transport
        self.allow_insecure = allow_insecure
        self._store = store

    def store(self, tenant_id: uuid.UUID) -> CredentialStore:
        if self._store is None:
            self._store = default_store(
                self.state_dir, tenant_id, allow_insecure=self.allow_insecure
            )
        return self._store

    def agent(self) -> Agent:
        config = load(self.config_path)
        store = self.store(config.tenant_id)
        server = SchoolOSClient(config, store.load, self.transport)
        tally = TallyClient(config.tally_url, self.tally_transport)

        def rotated(new: AgentConfig, secret: bytes) -> None:
            store.save(secret)
            save(new, self.config_path)

        return Agent(server, tally, on_key_rotated=rotated)


def cmd_enrol(rt: Runtime, args: argparse.Namespace) -> int:
    config = AgentConfig(
        server_url=args.server, tenant_id=uuid.UUID(args.school), tally_url=args.tally_url
    )
    store = rt.store(config.tenant_id)
    out = SchoolOSClient(config, lambda: b"", rt.transport).enrol(args.code, _platform())
    secret = base64.urlsafe_b64decode(str(out["secret"]) + "=" * (-len(str(out["secret"])) % 4))
    store.save(secret)
    enrolled = config.replace(
        device_id=uuid.UUID(str(out["device_id"])),
        key_id=str(out["key_id"]),
        key_rotated_at=dt.datetime.now(dt.UTC),
    )
    save(enrolled, rt.config_path)
    log.info("tally.agent.enrolled", extra={"code": "enrolled"})
    sys.stdout.write(
        "Enrolled. This PC's Tally agent can now sync. Choose the ledger groups on the Tally "
        "connector screen in SchoolOS.\n"
    )
    return EXIT_OK


def cmd_sync_once(rt: Runtime, args: argparse.Namespace) -> int:
    report = rt.agent().sync_once()
    sys.stdout.write(f"{report.outcome}: {report.parties} ledgers in {report.groups} groups\n")
    return EXIT_OK


def cmd_run(rt: Runtime, args: argparse.Namespace) -> int:
    stop = threading.Event()

    def _stop(signum: int, frame: FrameType | None) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, _stop)
    log.info("tally.agent.started", extra={"code": __version__})
    rt.agent().run(stop)
    log.info("tally.agent.stopped", extra={"code": "stopped"})
    return EXIT_OK


def cmd_status(rt: Runtime, args: argparse.Namespace) -> int:
    config = load(rt.config_path)
    lines = [
        f"agent version: {__version__}",
        f"server: {config.server_url}",
        f"school id: {config.tenant_id}",
        f"device id: {config.device_id or 'not enrolled'}",
        f"tally: {config.tally_url}",
        f"key last rotated: {config.key_rotated_at.isoformat() if config.key_rotated_at else '-'}",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return EXIT_OK


def cmd_rotate(rt: Runtime, args: argparse.Namespace) -> int:
    rt.agent().rotate()
    sys.stdout.write("The device key was rotated.\n")
    return EXIT_OK


def cmd_check_tally(rt: Runtime, args: argparse.Namespace) -> int:
    config = load(rt.config_path)
    tally = TallyClient(config.tally_url, rt.tally_transport)
    companies = parse_companies(tally.export(companies_request()))
    if not companies:
        sys.stdout.write("Tally answered, but no company is open. Open the school's company.\n")
        return EXIT_FAILED
    groups = parse_groups(tally.export(groups_request(companies[0])))
    sys.stdout.write(f"Tally answered: {len(companies)} company open, {len(groups)} groups.\n")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sos-tally-agent", description=__doc__.split("\n")[0])
    parser.add_argument("--state-dir", type=Path, default=None, help="default: per-user folder")
    parser.add_argument(
        "--insecure-file-store",
        action="store_true",
        help="development only: keep the device key in a file instead of Windows DPAPI",
    )
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    enrol = commands.add_parser("enrol", help="exchange the owner's one-time code for a key")
    enrol.add_argument("--server", required=True, help="SchoolOS address, https://...")
    enrol.add_argument("--school", required=True, help="the school id shown with the code")
    enrol.add_argument("--code", required=True, help="the one-time code (XXXX-XXXX-XXXX)")
    enrol.add_argument("--tally-url", default="http://127.0.0.1:9000")
    enrol.set_defaults(handler=cmd_enrol)
    commands.add_parser("run", help="sync every 30 minutes (the service)").set_defaults(
        handler=cmd_run
    )
    commands.add_parser("sync-once", help="sync now and exit").set_defaults(handler=cmd_sync_once)
    commands.add_parser("status", help="show the configuration").set_defaults(handler=cmd_status)
    commands.add_parser("rotate-key", help="get a new device key now").set_defaults(
        handler=cmd_rotate
    )
    commands.add_parser("check-tally", help="check Tally's XML server on this PC").set_defaults(
        handler=cmd_check_tally
    )
    return parser


def main(argv: Sequence[str] | None = None, *, runtime: Runtime | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_dir = runtime.state_dir if runtime is not None else args.state_dir or default_state_dir()
    setup_logging(state_dir, verbose=args.verbose)
    rt = runtime or Runtime(state_dir, allow_insecure=args.insecure_file_store)
    try:
        code: int = args.handler(rt, args)
    except (ConfigError, CredentialError) as err:
        sys.stderr.write(f"{err}\n")
        return EXIT_FAILED
    except AgentError as err:
        log.warning("tally.agent.failed", extra={"code": err.code})
        sys.stderr.write(f"Could not finish: {err.code}.\n")
        return EXIT_FAILED
    except TallyXmlError:
        sys.stderr.write("Tally's answer could not be read. Is TallyPrime open?\n")
        return EXIT_FAILED
    except ValueError as err:
        sys.stderr.write(f"{err}\n")
        return EXIT_FAILED
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
