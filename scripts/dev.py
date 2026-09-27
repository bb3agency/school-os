"""Run SchoolOS locally with the app processes on this machine: API, worker, beat and web.

    make dev-host            (or: .venv/Scripts/python scripts/dev.py   on Windows)
    make dev-host ARGS=--raw   every log line exactly as the service printed it
    make dev-stop              stop the backing containers (data volumes are kept)

WHY THIS EXISTS NEXT TO ``make dev``. ``make dev`` builds images and runs everything in
containers, which is what CI and the dedicated hosts do and stays the reference. For day-to-day
work a rebuild per change is slow, so this runs only the backing services in Docker (Postgres,
Valkey, SeaweedFS, the dev OIDC stub) and the four app processes on the host with reload:

    api     uvicorn app.main:app --reload          http://localhost:8000
    worker  celery worker (all queues)             (solo pool on Windows: no prefork there)
    beat    celery beat
    web     next dev                               http://localhost:3000

WHAT IT DOES FIRST, all idempotent and additive (it never drops, resets or deletes anything):

1. ``.env`` from ``.env.example`` if missing (the same as ``make dev``).
2. ``docker compose --profile dev up -d --wait db valkey s3 oidc`` and the buckets (s3-init's
   logic, from the host).
3. ``alembic upgrade head`` + audit partitions as ``sos_migrator`` (the compose ``migrate`` job).
4. ``python -m app.devtools.seed_synthetic``: synthetic schools only (invariant 11); the tool
   refuses outside SOS_ENV=local|ci and re-running it is idempotent. ``--no-seed`` skips it.

Then it prints a "Sign in as" cheat sheet (synthetic subjects from ``app.devtools.plan``, no
database) and the dev sign-in page, http://localhost:3000/en/dev/sign-in. The local OIDC stub
marks every staff sign-in as MFA, so typing a subject is enough; the app's MFA and step-up
checks are unchanged (apps/web/README.md, "Manual verification with the dev OIDC stub").

HOST NAMES. docker-compose.yml gives the app containers in-network URLs (db, valkey, s3, oidc).
Here every process is on the host, so the same settings point at localhost. The OIDC issuer is
``http://localhost:8080/...`` for the API, the BFF and the browser alike: ``oidc.localhost``
(used by the containers) resolves in browsers but not for Python or Node on Windows, and the
issuer in tokens must equal the one the BFF discovers. Secrets are read from ``.env`` into the
children's environment only; nothing here prints them.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "apps" / "api"
PY = sys.executable
WINDOWS = os.name == "nt"

BACKING = ["db", "valkey", "s3", "oidc"]
QUEUES = "ingest,embed,ocr,dq,exports,pdf,maintenance"  # docker-compose.yml worker command
APP_PORTS = {"api": 8000, "web": 3000}
WAIT_S = 120


@dataclass(frozen=True)
class Service:
    name: str
    command: list[str]
    cwd: Path
    url: str | None
    colour: str


# --- terminal ----------------------------------------------------------------------------

_COLOUR = sys.stdout.isatty()
_lock = threading.Lock()


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def emit(line: str) -> None:
    # Never let one line stop a pump: an unread pipe would stall that service.
    with _lock, contextlib.suppress(UnicodeError, OSError):
        print(line, flush=True)


def step(label: str, command: list[str], env: dict[str, str], cwd: Path = ROOT) -> bool:
    """Run a setup step; print its output only if it failed."""
    print(f"  {label}...", end="", flush=True)
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if done.returncode == 0:
        print(paint(" ok", "32"))
        return True
    print(paint(" failed", "1;31"))
    for line in (done.stdout + done.stderr).strip().splitlines()[-20:]:
        print(f"      {line}")
    return False


# --- environment -------------------------------------------------------------------------


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def store_ports(env: dict[str, str]) -> dict[str, int]:
    """Host ports of the backing services (SOS_DB_PORT / SOS_VALKEY_PORT, as in compose)."""
    return {
        "Postgres": int(env.get("SOS_DB_PORT") or 5432),
        "Valkey": int(env.get("SOS_VALKEY_PORT") or 6379),
        "SeaweedFS S3": 8333,
        "OIDC stub": 8080,
    }


def host_env(dotenv: dict[str, str]) -> dict[str, str]:
    """``.env`` plus the URLs docker-compose.yml builds for the containers, pointed at localhost."""
    env = {**os.environ, **dotenv}
    ports = store_ports(env)
    pg, valkey = ports["Postgres"], ports["Valkey"]

    def db(role: str, password_key: str) -> str:
        return f"postgresql+psycopg://{role}:{dotenv[password_key]}@localhost:{pg}/schoolos"

    env.update(
        {
            "SOS_DATABASE_URL": db("sos_app", "SOS_DB_APP_PASSWORD"),
            "SOS_PLATFORM_DATABASE_URL": db("sos_platform", "SOS_DB_PLATFORM_PASSWORD"),
            "SOS_MIGRATOR_DATABASE_URL": db("sos_migrator", "SOS_DB_MIGRATOR_PASSWORD"),
            "SOS_REDIS_URL": f"redis://localhost:{valkey}/0",
            "SOS_S3_ENDPOINT_URL": "http://localhost:8333",
            "SOS_S3_PRESIGN_ENDPOINT_URL": "http://localhost:8333",
            "SOS_OIDC_ISSUER": "http://localhost:8080/schoolos",
            "SOS_OIDC_JWKS_URI": "http://localhost:8080/schoolos/jwks",
            "SOS_PLATFORM_OIDC_ISSUER": "http://localhost:8080/platform",
            "SOS_PLATFORM_OIDC_JWKS_URI": "http://localhost:8080/platform/jwks",
            # Web (BFF) side.
            "API_INTERNAL_URL": "http://localhost:8000",
            "REDIS_URL": f"redis://localhost:{valkey}/1",
            "OIDC_ISSUER": "http://localhost:8080/schoolos",
            "PLATFORM_OIDC_ISSUER": "http://localhost:8080/platform",
            # Browser uploads go straight to presigned SeaweedFS URLs; next dev accepts this
            # plain-http loopback origin in img-src/connect-src (production is https-only).
            "FILES_ORIGIN": "http://localhost:8333",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


# --- setup -------------------------------------------------------------------------------


def container_ports(compose: list[str], env: dict[str, str]) -> set[int]:
    """Host ports already published by this project's own running containers."""
    done = subprocess.run(
        [*compose, "ps", "--format", "json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return {int(p) for p in re.findall(r'"PublishedPort":\s*(\d+)', done.stdout)} - {0}


def start_backing(env: dict[str, str]) -> bool:
    if shutil.which("docker") is None:
        print(paint("  docker is not on PATH: start Docker Desktop, then retry.", "1;31"))
        return False
    compose = ["docker", "compose", "--profile", "dev"]
    ours = container_ports(compose, env)
    taken = [
        f"{name} :{port}"
        for name, port in store_ports(env).items()
        if port not in ours and listening(port)
    ]
    if taken:
        print(paint(f"  already in use by something else: {', '.join(taken)}", "1;31"))
        print("  Pick free ports in .env, for example SOS_DB_PORT=5434 / SOS_VALKEY_PORT=6381.")
        return False
    if not step(
        "backing services (db, valkey, s3, oidc)",
        [*compose, "up", "-d", "--wait", "--wait-timeout", str(WAIT_S), *BACKING],
        env,
    ):
        return False
    missing = [f"{n} :{p}" for n, p in store_ports(env).items() if not listening(p)]
    if missing:
        print(paint(f"  not reachable from the host: {', '.join(missing)}", "1;31"))
        return False
    return True


BUCKETS = """
import boto3, os
s3 = boto3.client("s3", endpoint_url=os.environ["SOS_S3_ENDPOINT_URL"],
                  region_name=os.environ.get("AWS_REGION", "ap-south-1"))
existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
for name in (os.environ["SOS_S3_BUCKET_FILES"], os.environ["SOS_S3_BUCKET_AUDIT"]):
    if name not in existing:
        s3.create_bucket(Bucket=name)
"""


def prepare(env: dict[str, str], *, seed: bool) -> bool:
    ok = (
        step("buckets", [PY, "-c", BUCKETS], env)
        and step(
            "alembic upgrade head",
            [PY, "-m", "alembic", "-c", str(API_DIR / "alembic.ini"), "upgrade", "head"],
            env,
            API_DIR,
        )
        and step(
            "audit partitions",
            [PY, "-m", "app.audit.partitions", "--months-ahead", "12"],
            env,
            API_DIR,
        )
    )
    if ok and seed:
        ok = step("synthetic schools", [PY, "-m", "app.devtools.seed_synthetic"], env, API_DIR)
    return ok


# --- sign-in cheat sheet -----------------------------------------------------------------

# First person of each role in the first synthetic school, from the pure seed plan (no
# database, no secrets). Run with the project Python like the other steps so this file stays
# stdlib-only.
SIGN_IN_PLAN = """
import json
from app.devtools.plan import build_plan
school = build_plan().tenants[0]
print(json.dumps({"code": school.code,
                  "staff": [[s.role, s.subject] for s in school.staff if s.ordinal == 1]}))
"""
CHEAT_SHEET_ROLES = (
    "owner",
    "principal",
    "office_admin",
    "office_staff",
    "class_teacher",
    "teacher",
)
DEV_SIGN_IN_URL = "http://localhost:3000/en/dev/sign-in"


def sign_in_lines(plan_json: str) -> list[str]:
    """The "Sign in as" cheat sheet from SIGN_IN_PLAN's output (empty if it is unusable)."""
    try:
        plan = json.loads(plan_json)
        code = str(plan["code"])
        subjects = {str(role): str(subject) for role, subject in plan["staff"]}
    except (ValueError, KeyError, TypeError):
        return []
    lines = [f"  Sign in as ({code}; type the subject at the local sign-in page, no claims):"]
    lines += [
        f"    {role.ljust(14)} {subjects[role]}" for role in CHEAT_SHEET_ROLES if role in subjects
    ]
    lines.append(f"  All roles and schools: {DEV_SIGN_IN_URL}")
    return lines


def print_sign_in(env: dict[str, str]) -> None:
    done = subprocess.run(
        [PY, "-c", SIGN_IN_PLAN],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = sign_in_lines(done.stdout) if done.returncode == 0 else []
    for line in lines or [f"  Dev sign-in: {DEV_SIGN_IN_URL}"]:
        print(paint(line, "2") if line.startswith("    ") else line)


# --- services ----------------------------------------------------------------------------


def services(beat_schedule: Path) -> list[Service]:
    celery = [PY, "-m", "celery", "-A", "sos_worker.celery_app"]
    pool = ["--pool=solo"] if WINDOWS else ["--concurrency=2"]
    # next dev started with node directly: through npm.cmd, Ctrl-C on Windows stops at cmd's
    # "Terminate batch job (Y/N)?" prompt and can leave the dev server behind.
    node = shutil.which("node") or "node"
    next_bin = str(ROOT / "node_modules" / "next" / "dist" / "bin" / "next")
    return [
        Service(
            "api",
            [
                PY,
                "-m",
                "uvicorn",
                "app.main:app",
                "--reload",
                "--port",
                "8000",
                "--no-server-header",
            ],
            API_DIR,
            "http://localhost:8000/docs",
            "36",
        ),
        Service(
            "worker",
            [*celery, "worker", "-Q", QUEUES, "--loglevel=INFO", *pool],
            ROOT,
            None,
            "35",
        ),
        Service(
            "beat",
            [*celery, "beat", "--loglevel=INFO", f"--schedule={beat_schedule}"],
            ROOT,
            None,
            "34",
        ),
        Service(
            "web",
            [node, next_bin, "dev", "--port", "3000"],
            ROOT / "apps" / "web",
            "http://localhost:3000",
            "32",
        ),
    ]


_QUIET_KEYS = {"ts", "timestamp", "service", "version", "env", "logger", "level", "event"}


def compact(line: str) -> str:
    """One structured JSON log record as ``LEVEL event key=value ...`` (already redacted by
    the app's logging; this only reformats). Anything else passes through unchanged."""
    if not line.startswith("{"):
        return line
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return line
    if not isinstance(record, dict) or "event" not in record:
        return line
    level = str(record.get("level", "")).upper()
    rest = " ".join(f"{k}={v}" for k, v in record.items() if k not in _QUIET_KEYS)
    text = f"{level:<7} {record['event']} {rest}".rstrip()
    if level in ("ERROR", "CRITICAL"):
        return paint(text, "31")
    if level == "WARNING":
        return paint(text, "33")
    return text


def pump(service: Service, stream: IO[str], raw: bool, tail: list[str]) -> None:
    label = paint(service.name.rjust(6), service.colour)
    for chunk in stream:
        line = chunk.rstrip("\n")
        tail.append(line)
        del tail[:-40]
        emit(f"{label} | {line if raw else compact(line)}")


def preflight() -> dict[str, str] | None:
    """``.env`` (created if missing), SOS_ENV=local, free app ports; the children's env."""
    if not (ROOT / ".env").exists():
        shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        print("  created .env from .env.example")
    env = host_env(read_env_file(ROOT / ".env"))
    if env.get("SOS_ENV") not in ("local", "ci"):
        print(paint("  SOS_ENV in .env must be local: refusing to start.", "1;31"))
        return None
    busy = [f"{n} :{p}" for n, p in APP_PORTS.items() if listening(p)]
    if busy:
        print(paint(f"  already in use: {', '.join(busy)}.", "1;31"))
        print("  If `make dev` is running, stop its app containers first:")
        print("    docker compose stop api worker beat web")
        return None
    return env


Running = list[tuple[Service, "subprocess.Popen[str]", list[str]]]


def launch(env: dict[str, str], schedule: Path, *, raw: bool) -> Running:
    procs: Running = []
    for service in services(schedule):
        # A new process group on Windows so Ctrl-C reaches this process first and the
        # shutdown below stops the children (no orphaned reloaders holding the ports).
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
        proc = subprocess.Popen(
            service.command,
            cwd=service.cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )
        tail: list[str] = []
        if proc.stdout is not None:
            threading.Thread(
                target=pump, args=(service, proc.stdout, raw, tail), daemon=True
            ).start()
        procs.append((service, proc, tail))
        where = paint(service.url, "4") if service.url else paint("(no port)", "2")
        print(f"  {service.name.ljust(7)} {where}")
    return procs


def supervise(procs: Running) -> None:
    """Until Ctrl-C or until one service exits (then say what it said, and stop the rest)."""
    try:
        while True:
            for service, proc, tail in procs:
                if proc.poll() is None:
                    continue
                emit(paint(f"\n  {service.name} exited ({proc.returncode}). Last output:", "1;31"))
                for line in tail[-20:]:
                    emit(f"    {line}")
                return
            time.sleep(0.4)
    except KeyboardInterrupt:
        pass


def stop(procs: Running) -> None:
    print(paint("\n  stopping...", "2"))
    for _, proc, _ in procs:
        if proc.poll() is None:
            if WINDOWS:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.terminate()
    for _, proc, _ in procs:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    raw = "--raw" in sys.argv
    seed = "--no-seed" not in sys.argv
    if isinstance(sys.stdout, io.TextIOWrapper):
        # UTF-8 whatever the console code page (cp1252 cannot print Next's banner), and a
        # character the terminal cannot show becomes "?" instead of killing a reader thread.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    env = preflight()
    if env is None:
        return 2
    print(paint("SchoolOS dev (host processes)", "1"))
    if not start_backing(env) or not prepare(env, seed=seed):
        print(paint("  Setup failed: fix the above, then retry.", "1;31"))
        return 2
    schedule_dir = Path(tempfile.mkdtemp(prefix="schoolos-beat-"))
    procs: Running = []
    try:
        procs = launch(env, schedule_dir / "celerybeat-schedule", raw=raw)
        print_sign_in(env)
        print(paint("  Ctrl-C stops everything; `make dev-stop` stops the containers.", "2"))
        print(paint("  " + "-" * 70, "2"))
        supervise(procs)
    finally:
        stop(procs)
        shutil.rmtree(schedule_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
