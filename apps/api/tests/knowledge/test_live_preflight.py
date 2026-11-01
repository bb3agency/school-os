"""Offline preflight of the live evaluation (docs/06 §13.7, §13.8; ADR-0033; docs/10 §11.1).

No network and no database: every check reads the environment mapping given to it, models.yaml
and the evals/ files. Credentials are synthetic placeholders of the right shape.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.knowledge import live_preflight as pf
from app.knowledge.config.llm import load_llm_config

SA_JSON = json.dumps(
    {
        "type": "service_account",
        "client_email": "sos-eval@sos-eval-nonprod.iam.gserviceaccount.com",
        "private_key": "SYNTHETIC-NOT-A-KEY-7f3a9",
    }
)


def ready_env(**overrides: str) -> dict[str, str]:
    env = {
        "SOS_ENV": "local",
        "SOS_EVAL_LIVE_ACK": "synthetic-only",
        "SOS_LLM_GCP_PROJECT": "sos-eval-nonprod",
        "SOS_LLM_GCP_CREDENTIALS_SOURCE": "service-account-key",
        "SOS_LLM_GCP_CREDENTIALS_JSON": SA_JSON,
        "SOS_LLM_ZDR_CONFIRMED": "true",
        "SOS_TEST_ADMIN_DATABASE_URL": "postgresql+psycopg://admin:pw@localhost:5432/eval",
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v != ""}


def missing_names(env: dict[str, str], **kwargs: object) -> set[str]:
    result = pf.preflight(env, **kwargs)  # type: ignore[arg-type]
    return {c.name for c in result.missing}


def run(env: dict[str, str], *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = pf.main(list(argv), env=env, stdout=out)
    return code, out.getvalue()


def test_docs06_s13_7_ready_environment_passes_without_printing_secrets() -> None:
    code, out = run(ready_env())
    assert code == pf.EXIT_READY, out
    assert "READY" in out
    assert "MISSING" not in out
    for secret in ("SYNTHETIC-NOT-A-KEY-7f3a9", "sos-eval-nonprod", "admin:pw"):
        assert secret not in out
    # Provider and model per role, with the region product roles use.
    assert "role answer: gemini gemini-3.5-flash @ asia-south1 (product)" in out
    assert "role eval_judge:" in out
    assert "(offline only)" in out
    assert "cost estimate" in out
    assert "budget limits" in out
    out.encode("ascii")  # Windows consoles: plain ASCII output


def test_docs06_s13_7_empty_environment_lists_everything_missing() -> None:
    code, out = run({"SOS_TEST_ADMIN_DATABASE_URL": "x"})
    assert code == pf.EXIT_MISSING
    assert "NOT READY" in out
    for name in (
        "SOS_EVAL_LIVE_ACK",
        "SOS_LLM_GCP_PROJECT",
        "SOS_LLM_GCP_CREDENTIALS_SOURCE",
        "SOS_LLM_GCP_CREDENTIALS_JSON",
        "SOS_LLM_ZDR_CONFIRMED",
    ):
        assert f"  - {name}" in out


@pytest.mark.parametrize("env", ["staging", "prod", "qa"])
def test_docs06_s13_7_refuses_staging_and_prod_settings(env: str) -> None:
    assert missing_names(ready_env(SOS_ENV=env)) == {"SOS_ENV"}


@pytest.mark.parametrize(
    ("value", "ok"),
    [("true", True), ("1", True), ("on", True), ("false", False), ("maybe", False), ("", False)],
)
def test_adr0033_zdr_flag_parsed_like_the_startup_guard(value: str, ok: bool) -> None:
    missing = missing_names(ready_env(SOS_LLM_ZDR_CONFIRMED=value))
    assert ("SOS_LLM_ZDR_CONFIRMED" in missing) is not ok


def test_adr0033_cache_config_verification_must_stay_on() -> None:
    missing = missing_names(ready_env(SOS_LLM_VERIFY_CACHE_CONFIG="false"))
    assert missing == {"SOS_LLM_VERIFY_CACHE_CONFIG"}


@pytest.mark.parametrize(("location", "ok"), [("asia-south2", True), ("us-central1", False)])
def test_adr0033_india_region_only(location: str, ok: bool) -> None:
    missing = missing_names(ready_env(SOS_LLM_GCP_LOCATION=location))
    assert ("SOS_LLM_GCP_LOCATION" in missing) is not ok


@pytest.mark.parametrize("project", ["sos-prod", "schoolos-prod-ai", "Bad_Project"])
def test_docs06_s13_7_needs_a_valid_non_production_project(project: str) -> None:
    assert missing_names(ready_env(SOS_LLM_GCP_PROJECT=project)) == {"SOS_LLM_GCP_PROJECT"}


@pytest.mark.parametrize(
    "credential",
    [
        json.dumps({"type": "authorized_user", "client_id": "x", "refresh_token": "y"}),
        json.dumps({"type": "service_account", "client_email": "me@example.com"}),
        "not json",
        '{"type": "service_account", "dev-only": true}',
    ],
)
def test_invariant10_service_identity_only(credential: str) -> None:
    code, out = run(ready_env(SOS_LLM_GCP_CREDENTIALS_JSON=credential))
    assert code == pf.EXIT_MISSING
    assert "  - SOS_LLM_GCP_CREDENTIALS_JSON" in out
    assert "refresh_token" not in out


def test_invariant10_unknown_credential_source() -> None:
    missing = missing_names(ready_env(SOS_LLM_GCP_CREDENTIALS_SOURCE="api-key"))
    assert missing == {"SOS_LLM_GCP_CREDENTIALS_SOURCE", "SOS_LLM_GCP_CREDENTIALS_JSON"}


def test_database_needs_docker_or_an_admin_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    env = ready_env(SOS_TEST_ADMIN_DATABASE_URL="")
    assert missing_names(env) == {"database"}
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")
    assert missing_names(env) == set()


def test_adr0033_anthropic_roles_need_key_and_zdr(monkeypatch: pytest.MonkeyPatch) -> None:
    fallback = load_llm_config().use_fallback()
    monkeypatch.setattr(pf, "load_llm_config", lambda _path=None: fallback)
    assert missing_names(ready_env()) == {"SOS_ANTHROPIC_API_KEY", "SOS_ANTHROPIC_ZDR_CONFIRMED"}
    env = ready_env(SOS_ANTHROPIC_API_KEY="sk-synthetic", SOS_ANTHROPIC_ZDR_CONFIRMED="true")
    code, out = run(env)
    assert code == pf.EXIT_READY, out
    assert "sk-synthetic" not in out


def _copy_evals(tmp_path: Path) -> Path:
    target = tmp_path / "evals"
    shutil.copytree(pf.DEFAULT_EVALS_DIR / "datasets", target / "datasets")
    shutil.copy(pf.DEFAULT_EVALS_DIR / "gates.toml", target / "gates.toml")
    return target


def test_docs06_s13_8_english_first_gates_must_be_hard(tmp_path: Path) -> None:
    evals = _copy_evals(tmp_path)
    assert missing_names(ready_env(), evals_dir=evals) == set()
    gates = evals / "gates.toml"
    text = gates.read_text(encoding="utf-8")
    weakened = text.replace(
        'metric = "english_first_telugu_outputs"\nop = "=="\nthreshold = 0\nseverity = "hard"',
        'metric = "english_first_telugu_outputs"\nop = "=="\nthreshold = 0\nseverity = "soft"',
    )
    assert weakened != text
    gates.write_text(weakened, encoding="utf-8")
    code, out = run(ready_env(), "--evals-dir", str(evals))
    assert code == pf.EXIT_MISSING
    assert "english_first_telugu_outputs" in out


def test_docs06_s13_8_datasets_and_telugu_questions_present(tmp_path: Path) -> None:
    evals = _copy_evals(tmp_path)
    (evals / "datasets" / "fees.jsonl").unlink()
    assert "datasets" in missing_names(ready_env(), evals_dir=evals)
    evals = _copy_evals(tmp_path / "second")
    mixed = evals / "datasets" / "mixed_lang.jsonl"
    rows = [json.loads(line) for line in mixed.read_text(encoding="utf-8").splitlines() if line]
    english = [{**row, "locale": "en"} for row in rows]
    mixed.write_text("\n".join(json.dumps(r) for r in english) + "\n", encoding="utf-8")
    assert missing_names(ready_env(), evals_dir=evals) == {"english-first sets"}


def test_bad_arguments_exit_2() -> None:
    assert run(ready_env(), "--calls", "0")[0] == pf.EXIT_USAGE
    assert run(ready_env(), "--nope")[0] == pf.EXIT_USAGE


def test_entrypoint_reads_the_process_environment() -> None:
    """``make eval-live-preflight`` runs the script next to the eval bridge (exit 1 here)."""
    script = Path(__file__).with_name("eval_preflight.py")
    child = {k: v for k, v in os.environ.items() if not k.startswith("SOS_")}
    res = subprocess.run(
        [sys.executable, str(script)],
        env=child,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert res.returncode == pf.EXIT_MISSING, res.stderr
    assert "  - SOS_EVAL_LIVE_ACK" in res.stdout
    assert "Traceback" not in res.stderr
