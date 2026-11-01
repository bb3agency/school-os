"""``make eval-live-preflight``: offline readiness check for the live evaluation (docs/06 §13.7;
ADR-0033; docs/10 §11, §11.1).

Usage::

    python apps/api/tests/knowledge/eval_preflight.py [--evals-dir DIR] [--calls N]

Checks, WITHOUT any network call, what ``make eval-live`` needs and prints one line per check:

- ``models.yaml`` loads (the strict loader) and the provider and model of every role, with the
  Vertex location each role would use (product roles: ``SOS_LLM_GCP_LOCATION``; only the offline
  ``eval_judge`` may name its own);
- the environment is ``local`` or ``ci`` (the live run refuses staging/prod settings) and
  ``SOS_EVAL_LIVE_ACK=synthetic-only`` is set;
- Vertex AI in an India region (``asia-south1`` expected), a non-production project, a service
  identity (``SOS_LLM_GCP_CREDENTIALS_SOURCE`` plus a credential JSON of that kind, checked for
  shape only), ``SOS_LLM_ZDR_CONFIRMED=true`` exactly as the staging/prod start-up guard expects,
  and cache-config verification left on;
- the Anthropic key only when a role (the eval judge included) runs on Anthropic;
- a database for the throwaway stack (Docker on PATH or ``SOS_TEST_ADMIN_DATABASE_URL``);
- the evaluation datasets, the Telugu and code-mixed questions the English-first pass replays and
  the hard gates of §13.7 and §13.8 in ``evals/gates.toml``;
- the budget limits of ``models.yaml`` and a worst-case cost estimate for the run.

Only environment variable NAMES are printed, never their values (credentials are parsed in memory
for their shape and discarded). Exit codes: 0 ready, 1 something is missing (listed at the end),
2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal, TextIO

from pydantic import TypeAdapter, ValidationError

from app.core.config import (
    INDIA_GCP_LOCATIONS,
    Environment,
    LlmCredentialsSource,
    gcp_credential_problem,
)
from app.knowledge.config.llm import LlmConfig, load_llm_config

EXIT_READY: Final = 0
EXIT_MISSING: Final = 1
EXIT_USAGE: Final = 2

REPO_ROOT: Final = Path(__file__).resolve().parents[4]
DEFAULT_EVALS_DIR: Final = REPO_ROOT / "evals"
EXPECTED_LOCATION: Final = "asia-south1"
LIVE_ACK: Final = "synthetic-only"
# docs/06 s13.7: "roughly the full suite's ~700 model calls" (an estimate for display only).
DEFAULT_CALLS: Final = 700
ALLOWED_ENVS: Final = frozenset({Environment.LOCAL, Environment.CI})
PROD_PROJECT_RE: Final = re.compile(r"(^|-)(prod|production)(-|$)")
PROJECT_RE: Final = re.compile(r"^[a-z][a-z0-9\-]{4,28}[a-z0-9]$")

DATASET_FILES: Final = (
    "corpus.jsonl",
    "records.jsonl",
    "documents.jsonl",
    "mixed_lang.jsonl",
    "permissions.jsonl",
    "adversarial.jsonl",
    "unanswerable.jsonl",
    "temporal.jsonl",
    "circulars.jsonl",
    "fees.jsonl",
    "contextual.jsonl",
    "conversations.jsonl",
)
# Hard gates that must exist before a role may go live (docs/06 s13.7) and the English-first
# gates (s13.8, ADR-0036).
REQUIRED_HARD_GATES: Final = (
    "leakage_count",
    "injection_success_count",
    "citation_precision",
    "refusal_correctness",
    "circular_deadline_recall",
    "circular_hallucinated_deadlines",
    "fee_figure_accuracy",
    "fee_leakage_count",
    "english_first_telugu_outputs",
    "english_first_english_answer_rate",
)
REQUIRED_SOFT_GATES: Final = ("recall_at_10", "mrr_at_10", "citation_coverage", "language_match")

Status = Literal["ok", "missing", "info"]
_BOOL: Final = TypeAdapter(bool)


@dataclass(frozen=True, slots=True)
class Check:
    status: Status
    name: str
    detail: str


@dataclass(frozen=True, slots=True)
class Result:
    checks: tuple[Check, ...]

    @property
    def missing(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status == "missing")

    @property
    def ready(self) -> bool:
        return not self.missing


def _set(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    return value if value is not None and value.strip() else None


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool | None:
    """The boolean as pydantic-settings parses it (None if it does not parse)."""
    raw = _set(env, name)
    if raw is None:
        return default
    try:
        return _BOOL.validate_python(raw.strip())
    except ValidationError:
        return None


def _check_models(config: LlmConfig, location: str) -> list[Check]:
    out: list[Check] = []
    for name in sorted(config.roles):
        role = config.roles[name]
        where = (role.location or location) if role.provider == "gemini" else "anthropic api"
        scope = "offline only" if role.offline_only else "product"
        out.append(
            Check("info", f"role {name}", f"{role.provider} {role.model} @ {where} ({scope})")
        )
    return out


def _check_environment(env: Mapping[str, str]) -> list[Check]:
    out: list[Check] = []
    raw_env = (_set(env, "SOS_ENV") or Environment.LOCAL.value).strip()
    try:
        current = Environment(raw_env)
    except ValueError:
        out.append(Check("missing", "SOS_ENV", "not a known environment (local or ci)"))
    else:
        if current in ALLOWED_ENVS:
            out.append(Check("ok", "SOS_ENV", f"{current.value} (live runs refuse staging/prod)"))
        else:
            out.append(
                Check(
                    "missing",
                    "SOS_ENV",
                    f"{current.value}: the live evaluation runs only in "
                    "local or ci, never with staging/prod settings",
                )
            )
    if env.get("SOS_EVAL_LIVE_ACK") == LIVE_ACK:
        out.append(Check("ok", "SOS_EVAL_LIVE_ACK", f"set to {LIVE_ACK}"))
    else:
        out.append(
            Check(
                "missing",
                "SOS_EVAL_LIVE_ACK",
                f"set it to {LIVE_ACK} (the synthetic corpus is sent to the provider and billed)",
            )
        )
    return out


def _check_location(env: Mapping[str, str]) -> tuple[Check, str]:
    name = "SOS_LLM_GCP_LOCATION"
    location = (_set(env, name) or EXPECTED_LOCATION).strip()
    if location == EXPECTED_LOCATION:
        return Check("ok", name, f"{location} (default region, ADR-0033)"), location
    if location in INDIA_GCP_LOCATIONS:
        detail = f"{location} (India region; models.yaml was checked against {EXPECTED_LOCATION})"
        return Check("ok", name, detail), location
    regions = ", ".join(sorted(INDIA_GCP_LOCATIONS))
    return Check("missing", name, f"{location} is not an India region ({regions})"), location


def _check_project(env: Mapping[str, str]) -> Check:
    name = "SOS_LLM_GCP_PROJECT"
    project = _set(env, name)
    if project is None:
        return Check("missing", name, "not set (a non-production project)")
    if not PROJECT_RE.match(project.strip()):
        return Check("missing", name, "not a valid Google project ID")
    if PROD_PROJECT_RE.search(project.strip()):
        return Check("missing", name, "looks like a production project; use a non-production one")
    return Check("ok", name, "set")


def _check_credentials(env: Mapping[str, str]) -> list[Check]:
    """Service identity only (invariant 10): the kind and the JSON shape, never the value."""
    source_name, json_name = "SOS_LLM_GCP_CREDENTIALS_SOURCE", "SOS_LLM_GCP_CREDENTIALS_JSON"
    raw_source = _set(env, source_name)
    source: LlmCredentialsSource | None = None
    kinds = "workload-identity or service-account-key"
    if raw_source is None:
        out = [Check("missing", source_name, f"not set ({kinds})")]
    else:
        try:
            source = LlmCredentialsSource(raw_source.strip())
        except ValueError:
            out = [Check("missing", source_name, f"must be {kinds}")]
        else:
            out = [Check("ok", source_name, source.value)]
    raw_json = _set(env, json_name)
    if raw_json is None:
        detail = "not set (service identity JSON from Secrets Manager; never a person's login)"
        out.append(Check("missing", json_name, detail))
    elif source is None:
        out.append(Check("missing", json_name, f"set, but needs a valid {source_name}"))
    elif "dev-only" in raw_json:
        out.append(Check("missing", json_name, "is a dev-only placeholder"))
    else:
        problem = gcp_credential_problem(source, raw_json)
        if problem is None:
            detail = f"a {source.value} credential (shape checked offline)"
            out.append(Check("ok", json_name, detail))
        else:
            out.append(Check("missing", json_name, problem))
    return out


def _check_zdr(env: Mapping[str, str]) -> list[Check]:
    out: list[Check] = []
    if _flag(env, "SOS_LLM_ZDR_CONFIRMED", default=False) is True:
        detail = "true (Vertex project set up for Zero Data Retention, docs/10 s11.1)"
        out.append(Check("ok", "SOS_LLM_ZDR_CONFIRMED", detail))
    else:
        detail = (
            "must be true after the ZDR steps of docs/10 s11.1 (caching off, no request-response "
            "logging, abuse-monitoring exception requested), as the staging/prod start-up guard "
            "requires"
        )
        out.append(Check("missing", "SOS_LLM_ZDR_CONFIRMED", detail))
    if _flag(env, "SOS_LLM_VERIFY_CACHE_CONFIG", default=True) is True:
        out.append(Check("ok", "SOS_LLM_VERIFY_CACHE_CONFIG", "on (cacheConfig read, fail closed)"))
    else:
        out.append(Check("missing", "SOS_LLM_VERIFY_CACHE_CONFIG", "must stay on"))
    return out


def _check_vertex(env: Mapping[str, str]) -> tuple[list[Check], str]:
    location_check, location = _check_location(env)
    checks = [location_check, _check_project(env), *_check_credentials(env), *_check_zdr(env)]
    return checks, location


def _check_anthropic(env: Mapping[str, str], config: LlmConfig) -> list[Check]:
    roles = config.anthropic_roles()
    if not roles:
        return [Check("info", "SOS_ANTHROPIC_API_KEY", "not needed (no role runs on Anthropic)")]
    out: list[Check] = []
    label = ", ".join(roles)
    if _set(env, "SOS_ANTHROPIC_API_KEY") is None:
        out.append(Check("missing", "SOS_ANTHROPIC_API_KEY", f"needed by roles: {label}"))
    else:
        out.append(Check("ok", "SOS_ANTHROPIC_API_KEY", f"set (roles: {label})"))
    if _flag(env, "SOS_ANTHROPIC_ZDR_CONFIRMED", default=False) is not True:
        out.append(
            Check(
                "missing",
                "SOS_ANTHROPIC_ZDR_CONFIRMED",
                "must be true while a role uses "
                "Anthropic (ZDR agreement and DPA signed, docs/10 s11)",
            )
        )
    return out


def _check_database(env: Mapping[str, str]) -> Check:
    if _set(env, "SOS_TEST_ADMIN_DATABASE_URL") is not None:
        return Check("ok", "database", "SOS_TEST_ADMIN_DATABASE_URL set")
    if shutil.which("docker") is not None:
        return Check("ok", "database", "docker found on PATH (testcontainers; not started here)")
    return Check("missing", "database", "needs Docker on PATH or SOS_TEST_ADMIN_DATABASE_URL")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _check_datasets(evals_dir: Path) -> list[Check]:
    out: list[Check] = []
    datasets = evals_dir / "datasets"
    absent = [name for name in DATASET_FILES if not (datasets / name).is_file()]
    if absent:
        out.append(Check("missing", "datasets", f"absent in {datasets}: {', '.join(absent)}"))
        return out
    out.append(Check("ok", "datasets", f"{len(DATASET_FILES)} files in evals/datasets"))
    try:
        mixed = _read_jsonl(datasets / "mixed_lang.jsonl")
        conversations = _read_jsonl(datasets / "conversations.jsonl")
    except (ValueError, OSError) as exc:
        out.append(Check("missing", "english-first sets", f"unreadable: {type(exc).__name__}"))
        return out
    telugu = sum(1 for row in mixed if row.get("locale") == "te")
    fast = sum(1 for row in mixed if row.get("locale") == "te" and row.get("fast") is True)
    if telugu and fast:
        out.append(
            Check(
                "ok",
                "english-first sets",
                f"{telugu} Telugu/code-mixed questions ({fast} in "
                f"the fast suite), {len(conversations)} conversations replayed with Telugu off",
            )
        )
    else:
        out.append(
            Check(
                "missing",
                "english-first sets",
                "mixed_lang.jsonl has no Telugu questions "
                "for the English-first pass (docs/06 s13.8)",
            )
        )
    return out


def _check_gates(evals_dir: Path) -> list[Check]:
    path = evals_dir / "gates.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [Check("missing", "gates", f"{path.name} unreadable: {type(exc).__name__}")]
    gates = data.get("gate", [])
    severity = {str(g.get("metric")): str(g.get("severity")) for g in gates if isinstance(g, dict)}
    out: list[Check] = []
    not_hard = [m for m in REQUIRED_HARD_GATES if severity.get(m) != "hard"]
    if not_hard:
        out.append(Check("missing", "hard gates", f"absent or not hard: {', '.join(not_hard)}"))
    else:
        out.append(
            Check(
                "ok",
                "hard gates",
                f"{len(REQUIRED_HARD_GATES)} required (docs/06 s13.7, "
                "s13.8) present; eval-live also fails on soft gates",
            )
        )
    absent_soft = [m for m in REQUIRED_SOFT_GATES if m not in severity]
    if absent_soft:
        out.append(Check("missing", "soft gates", f"absent: {', '.join(absent_soft)}"))
    return out


def _worst_case_call_usd(config: LlmConfig) -> Decimal:
    """The dearest product role's reservation: what the gateway reserves before one call."""
    tokens = Decimal(config.budget.reservation.input_tokens)
    worst = Decimal(0)
    for role in config.roles.values():
        price = config.prices[role.model]
        cost = tokens * price.input_usd_per_mtok + Decimal(role.max_output_tokens) * (
            price.output_usd_per_mtok
        )
        worst = max(worst, cost / Decimal(1_000_000))
    return worst


def _check_budget(config: LlmConfig, calls: int) -> list[Check]:
    budget = config.budget
    per_call = _worst_case_call_usd(config)
    total = per_call * calls
    inr = total * budget.usd_inr_rate
    return [
        Check(
            "info",
            "budget limits",
            f"alert at {budget.alert_fraction:.0%}, search-only at {budget.degrade_fraction:.0%}; "
            f"reservation {budget.reservation.input_tokens} input tokens per call; "
            f"{config.rate_limit.requests_per_minute_per_tenant} calls/min per school; "
            f"{config.limits.max_tool_rounds} tool rounds",
        ),
        Check(
            "info",
            "cost estimate",
            f"worst case ${per_call:.4f} per call x {calls} calls = ${total:.2f} "
            f"(about Rs {inr:.0f} at {budget.usd_inr_rate}); prices checked "
            f"{config.prices_checked_on.isoformat()}",
        ),
    ]


def preflight(
    env: Mapping[str, str],
    *,
    evals_dir: Path = DEFAULT_EVALS_DIR,
    models_path: Path | None = None,
    calls: int = DEFAULT_CALLS,
) -> Result:
    """Every check, offline. ``env`` is the process environment (tests pass their own)."""
    checks: list[Check] = []
    checks += _check_environment(env)
    vertex, location = _check_vertex(env)
    checks += vertex
    try:
        config = load_llm_config(models_path)
    except (ValidationError, ValueError, OSError) as exc:
        checks.append(Check("missing", "models.yaml", f"does not load: {type(exc).__name__}"))
        config = None
    if config is not None:
        checks.append(
            Check(
                "ok",
                "models.yaml",
                f"version {config.version}, default provider "
                f"{config.default_provider}, {len(config.roles)} roles",
            )
        )
        checks += _check_models(config, location)
        checks += _check_anthropic(env, config)
        checks += _check_budget(config, calls)
    checks.append(_check_database(env))
    checks += _check_datasets(evals_dir)
    checks += _check_gates(evals_dir)
    return Result(tuple(checks))


def render(result: Result, out: TextIO) -> None:
    labels = {"ok": "ok     ", "missing": "MISSING", "info": "info   "}
    for check in result.checks:
        out.write(f"{labels[check.status]} {check.name}: {check.detail}\n")
    if result.ready:
        out.write("\nlive evaluation preflight: READY (run: make eval-live EVAL_SUITE=full)\n")
        return
    out.write(f"\nlive evaluation preflight: NOT READY, {len(result.missing)} missing:\n")
    for check in result.missing:
        out.write(f"  - {check.name}\n")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="make eval-live-preflight",
        description="Offline readiness check for make eval-live (docs/06 s13.7). No network.",
    )
    p.add_argument("--evals-dir", type=Path, default=DEFAULT_EVALS_DIR)
    p.add_argument("--calls", type=int, default=DEFAULT_CALLS, help="model calls to estimate")
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str],
    stdout: TextIO | None = None,
) -> int:
    """``env`` is the process environment, passed in by the entrypoint script next to the eval
    bridge (only ``app.core.config`` reads the environment inside the app package)."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_READY
    if args.calls < 1:
        (stdout or sys.stdout).write("--calls must be at least 1\n")
        return EXIT_USAGE
    result = preflight(env, evals_dir=args.evals_dir, calls=args.calls)
    render(result, stdout or sys.stdout)
    return EXIT_READY if result.ready else EXIT_MISSING
