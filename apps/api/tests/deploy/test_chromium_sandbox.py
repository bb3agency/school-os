"""Chromium renders PDFs with its sandbox on in both tiers (ADR-0025; docs/07 §10, docs/10 §6, §15;
FR-EXP-002, SEC-030, NFR-SEC-005).

Dedicated tier (option D): only the worker container leaves docker-default, for a seccomp profile
(docker-default + chroot/clone/unshare) and an AppArmor profile (docker-default + ``userns,``) that
the host scripts install before the worker starts. Shared tier (option A): the ``pdf`` queue runs
on EC2 capacity whose Docker daemon uses the same seccomp profile as its default; the Fargate
worker no longer consumes ``pdf``. These tests read the deploy files; the sandboxed render itself
runs in CI (infra/docker/worker-pdf-sandbox-check.sh) against the built worker image.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
DEDICATED = REPO / "deploy" / "dedicated"
SECURITY = DEDICATED / "security"
SECCOMP = SECURITY / "seccomp-worker.json"
APPARMOR = SECURITY / "apparmor-schoolos-worker"
SCRIPTS = DEDICATED / "scripts"
CAPACITY = REPO / "infra" / "terraform" / "modules" / "ecs_ec2_capacity"
CI = REPO / ".github" / "workflows" / "ci.yml"

HOST_SECCOMP_PATH = "/etc/schoolos/security/seccomp-worker.json"
SANDBOX_SYSCALLS = {"chroot", "clone", "unshare"}
# Never allowed without a capability the containers do not have (docker-default gates them on
# CAP_SYS_ADMIN and friends; the sandbox profile must not widen them).
GATED = {
    "setns", "mount", "umount2", "bpf", "pivot_root", "open_by_handle_at", "init_module",
    "finit_module", "delete_module", "kexec_load", "reboot", "swapon", "swapoff", "syslog",
    "perf_event_open", "fsopen", "fsmount", "move_mount", "open_tree", "lookup_dcookie",
    "quotactl", "sethostname", "setdomainname", "acct", "settimeofday", "clock_settime",
}  # fmt: skip


def _profile() -> dict[str, Any]:
    profile: dict[str, Any] = json.loads(SECCOMP.read_text(encoding="utf-8"))
    return profile


def _unconditional_allows(profile: dict[str, Any]) -> set[str]:
    return {
        name
        for rule in profile["syscalls"]
        if rule["action"] == "SCMP_ACT_ALLOW"
        and not rule.get("args")
        and not rule.get("includes", {}).get("caps")
        for name in rule["names"]
    }


# --- the seccomp profile --------------------------------------------------------------------------


def test_SEC_030_seccomp_profile_is_docker_default_plus_the_sandbox_syscalls() -> None:
    profile = _profile()
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO", "deny by default, like docker-default"
    last = profile["syscalls"][-1]
    assert set(last["names"]) == SANDBOX_SYSCALLS
    assert last["action"] == "SCMP_ACT_ALLOW"
    assert not {"args", "includes", "excludes"} & set(last), "allowed without conditions"
    assert "ADR-0025" in last["comment"]
    # docker-default's flag-filtered clone rules are gone (clone is allowed outright instead) ...
    assert not [r for r in profile["syscalls"] if r["names"] == ["clone"] and r.get("args")]
    # ... and nothing else was widened: the capability-gated syscalls stay gated.
    allowed = _unconditional_allows(profile)
    assert not allowed & GATED, f"sandbox profile allows gated syscalls: {allowed & GATED}"
    sys_admin = [
        r for r in profile["syscalls"] if r.get("includes", {}).get("caps") == ["CAP_SYS_ADMIN"]
    ]
    assert sys_admin
    assert "setns" in sys_admin[0]["names"], "setns stays CAP_SYS_ADMIN-only"
    # clone3 keeps answering ENOSYS (glibc falls back to clone, which is filtered above).
    clone3 = [r for r in profile["syscalls"] if r["names"] == ["clone3"]]
    assert clone3
    assert (clone3[0]["action"], clone3[0]["errnoRet"]) == ("SCMP_ACT_ERRNO", 38)


def test_SEC_030_seccomp_profile_is_what_the_derivation_script_produces() -> None:
    """derive-seccomp.py documents the delta; re-applying it to the profile changes nothing."""
    spec = importlib.util.spec_from_file_location("derive_seccomp", SECURITY / "derive-seccomp.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rule: dict[str, Any] = module.SANDBOX_RULE
    assert set(rule["names"]) == SANDBOX_SYSCALLS
    assert _profile()["syscalls"][-1] == rule
    assert SECCOMP.read_text(encoding="utf-8").endswith("}\n")


# --- the AppArmor profile -------------------------------------------------------------------------


def test_SEC_030_apparmor_profile_is_docker_default_plus_userns() -> None:
    text = APPARMOR.read_text(encoding="utf-8")
    rules = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    rules = [r for r in rules if r]
    assert "abi <abi/4.0>," in rules, "AppArmor 4 syntax (Ubuntu 24.04) so `userns,` is mediated"
    assert "profile schoolos-worker flags=(attach_disconnected,mediate_deleted) {" in rules
    assert "userns," in rules
    for kept in (
        "deny mount,",
        "deny network alg,",
        "deny network vsock,",
        "deny @{PROC}/sysrq-trigger rwklx,",
        "deny @{PROC}/kcore rwklx,",
        "deny /sys/firmware/** rwklx,",
        "deny /sys/kernel/security/** rwklx,",
        "signal (send,receive) peer=schoolos-worker,",
        "ptrace (trace,tracedby,read,readby) peer=schoolos-worker,",
    ):
        assert kept in rules, f"docker-default rule missing: {kept}"
    assert not [r for r in rules if r.startswith(("change_profile", "pivot_root", "mount "))]
    assert "flags=(unconfined" not in text
    assert "complain" not in text


# --- compose: only the worker leaves docker-default -----------------------------------------------


def _compose() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load((DEDICATED / "compose.yaml").read_text(encoding="utf-8"))
    return doc


def test_SEC_030_only_the_dedicated_worker_uses_the_sandbox_profiles() -> None:
    services = _compose()["services"]
    worker = services["worker"]
    assert worker["security_opt"] == [
        "no-new-privileges:true",
        f"seccomp={HOST_SECCOMP_PATH}",
        "apparmor=schoolos-worker",
    ]
    assert worker["cap_drop"] == ["ALL"]
    assert "cap_add" not in worker
    assert worker["read_only"] is True
    assert worker["user"] == "10001:10001"
    for name, svc in services.items():
        if name == "worker":
            continue
        assert svc["security_opt"] == ["no-new-privileges:true"], f"{name} keeps docker-default"
    for svc in services.values():
        assert not [o for o in svc["security_opt"] if "unconfined" in o]
        assert not svc.get("privileged")


def test_SEC_030_host_scripts_install_the_profiles_where_compose_reads_them() -> None:
    lib = (SCRIPTS / "lib.sh").read_text(encoding="utf-8")
    assert 'SOS_SECCOMP_DIR="${SOS_SECCOMP_DIR:-$SOS_ETC/security}"' in lib
    assert 'SOS_ETC="${SOS_ETC:-/etc/schoolos}"' in lib
    assert '"$SOS_SECCOMP_DIR/seccomp-worker.json"' in lib
    assert '"$SOS_APPARMOR_DIR/schoolos-worker"' in lib
    assert "--replace" in lib


def _code(path: Path) -> list[str]:
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")]


def test_SEC_030_bootstrap_installs_the_profiles_before_the_stack_starts() -> None:
    code = _code(SCRIPTS / "bootstrap-host.sh")
    assert code.index('install_host_profiles "$release_dir"') < code.index("start_stack")


def test_SEC_030_upgrade_installs_the_profiles_of_the_release_it_runs() -> None:
    code = _code(SCRIPTS / "upgrade.sh")
    activate = code.index('activate_release "$version"')
    install = code.index('install_host_profiles "$(active_release_dir)"')
    trap = code.index("trap rollback ERR")
    restart = next(i for i, ln in enumerate(code) if ln.startswith("for svc in "))
    assert trap < activate < install < restart, (
        "profiles follow the release, under the rollback trap"
    )
    rollback = code.index("rollback() {")
    assert any(
        ln.startswith('install_host_profiles "$(active_release_dir)" ||')
        for ln in code[rollback:activate]
    ), "a rollback reinstalls the previous release's profiles"


def test_SEC_030_bundle_ships_the_profiles_and_the_host_loads_apparmor_first() -> None:
    assert '"$src/security"' in (SCRIPTS / "package.sh").read_text(encoding="utf-8")
    unit = (DEDICATED / "systemd" / "schoolos.service").read_text(encoding="utf-8")
    assert re.search(r"(?m)^After=.*\bapparmor\.service\b", unit)
    cloud_init = (
        REPO / "infra/terraform/modules/dedicated_host/templates/cloud-init.yaml.tftpl"
    ).read_text(encoding="utf-8")
    assert re.search(r"(?m)^  - apparmor\b", cloud_init), "cloud-init installs apparmor_parser"


def _bash() -> str | None:
    if sys.platform == "win32":
        # Git for Windows' bash (System32\bash.exe would start WSL instead).
        git = shutil.which("git")
        for parent in Path(git).resolve().parents if git else ():
            candidate = parent / "bin" / "bash.exe"
            if candidate.exists():
                return str(candidate)
        return None
    return shutil.which("bash")


def _install(tmp_path: Path, release: Path, parser_rc: int) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    if bash is None or shutil.which("jq") is None:
        pytest.skip("bash and jq are needed")
    parser = tmp_path / "apparmor_parser"
    parser.write_text(
        f'#!/usr/bin/env bash\necho "$@" > "{(tmp_path / "parser.args").as_posix()}"\n'
        f"exit {parser_rc}\n",
        encoding="utf-8",
        newline="\n",
    )
    parser.chmod(0o755)
    env = {
        **os.environ,
        "SOS_ETC": (tmp_path / "etc").as_posix(),
        "SOS_APPARMOR_DIR": (tmp_path / "apparmor.d").as_posix(),
        "SOS_APPARMOR_PARSER": parser.as_posix(),
        "SOS_LIB": (SCRIPTS / "lib.sh").as_posix(),
        "RELEASE": release.as_posix(),
    }
    # lib.sh pins PATH for hosts; keep the caller's PATH so jq and install are found here.
    script = 'p="$PATH"; . "$SOS_LIB"; PATH="$p"; install_host_profiles "$RELEASE"'
    return subprocess.run(
        [bash, "-c", script], env=env, capture_output=True, text=True, timeout=60, check=False
    )


def test_SEC_030_install_host_profiles_installs_and_loads(tmp_path: Path) -> None:
    result = _install(tmp_path, DEDICATED, parser_rc=0)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "etc/security/seccomp-worker.json").read_bytes() == SECCOMP.read_bytes()
    assert (tmp_path / "apparmor.d/schoolos-worker").read_bytes() == APPARMOR.read_bytes()
    args = (tmp_path / "parser.args").read_text(encoding="utf-8").split()
    assert args[:2] == ["--replace", "--write-cache"]
    assert args[-1].endswith("schoolos-worker")


def test_SEC_030_install_host_profiles_fails_when_apparmor_refuses(tmp_path: Path) -> None:
    result = _install(tmp_path, DEDICATED, parser_rc=1)
    assert result.returncode == 1, "a failure returns 1 so upgrade.sh's ERR trap rolls back"
    assert "could not load the AppArmor profile" in result.stderr


def test_SEC_030_install_host_profiles_skips_a_release_without_profiles(tmp_path: Path) -> None:
    old = tmp_path / "old-release"
    old.mkdir()
    result = _install(tmp_path, old, parser_rc=1)
    assert result.returncode == 0, result.stderr
    assert "no worker sandbox profiles" in result.stderr
    assert not (tmp_path / "parser.args").exists()


# --- CI renders with the sandbox on ---------------------------------------------------------------


def test_FR_EXP_002_ci_renders_a_pdf_with_the_sandbox_on() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert "infra/docker/worker-pdf-sandbox-check.sh" in ci
    assert "--apparmor schoolos-worker" in ci
    assert "kernel.apparmor_restrict_unprivileged_userns=1" in ci
    check = (REPO / "infra/docker/worker-pdf-sandbox-check.sh").read_text(encoding="utf-8")
    assert "worker-pdf-smoke.py --sandbox" in check
    assert "deploy/dedicated/security/seccomp-worker.json" in check
