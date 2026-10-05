"""Dedicated tier: each app container has its own AWS role (audit W3-06; docs/10 §15).

The internet-facing api must not be able to tag school files ``sos-lifecycle=discarded`` and
delete them. On a dedicated host every container used to share the instance role through IMDS (hop
limit 2).
Now the hop limit is 1 (no container reaches IMDS), and the host writes each container its own
role's short-lived credentials (``scripts/app-credentials.sh``, refreshed by a timer): the api role
(no tag, no delete; asserted by ``modules/dedicated_host/tests``), the worker role, and for WAL-G
the instance role. These tests read the deploy files and run ``refresh_app_credentials`` with a
fake ``aws`` command.
"""

from __future__ import annotations

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
SCRIPTS = DEDICATED / "scripts"
SYSTEMD = DEDICATED / "systemd"
HOST_TF = REPO / "infra" / "terraform" / "modules" / "dedicated_host"


def _compose(name: str = "compose.yaml") -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load((DEDICATED / name).read_text(encoding="utf-8"))
    return doc


def _aws_mounts(spec: dict[str, Any]) -> list[str]:
    return [v for v in spec.get("volumes") or [] if "/var/lib/schoolos/aws" in v]


def test_W3_06_api_and_worker_mount_only_their_own_credentials() -> None:
    services = _compose()["services"]
    assert _aws_mounts(services["api"]) == ["/var/lib/schoolos/aws/api:/run/aws:ro"]
    assert _aws_mounts(services["worker"]) == ["/var/lib/schoolos/aws/worker:/run/aws:ro"]
    for name in ("api", "worker"):
        assert services[name]["environment"]["AWS_CONFIG_FILE"] == "/run/aws/config"
    for name, spec in services.items():
        if name not in ("api", "worker"):
            assert not _aws_mounts(spec), f"{name} gets no AWS credentials"
    walg = _compose("compose.walg.yaml")["services"]["db"]
    assert _aws_mounts(walg) == ["/var/lib/schoolos/aws/walg:/run/aws:ro"]
    assert walg["environment"]["AWS_SHARED_CREDENTIALS_FILE"] == "/run/aws/credentials"


def test_W3_06_no_container_reaches_the_instance_role() -> None:
    main = (HOST_TF / "main.tf").read_text(encoding="utf-8")
    assert re.search(r"http_put_response_hop_limit\s*=\s*var\.imds_hop_limit", main)
    variables = (HOST_TF / "variables.tf").read_text(encoding="utf-8")
    block = variables.split('variable "imds_hop_limit"', 1)[1].split("\nvariable ", 1)[0]
    assert re.search(r"default\s*=\s*1\b", block)


def test_W3_06_credentials_are_written_before_the_stack_and_kept_fresh() -> None:
    unit = (SYSTEMD / "schoolos.service").read_text(encoding="utf-8")
    pre = re.findall(r"(?m)^ExecStartPre=(.+)$", unit)
    assert pre[-1].endswith("/scripts/app-credentials.sh")
    assert re.search(r"(?m)^ExecStart=.*compose\.sh up", unit)
    timer = (SYSTEMD / "schoolos-app-credentials.timer").read_text(encoding="utf-8")
    m = re.search(r"(?m)^OnUnitActiveSec=(\d+)min$", timer)
    assert m is not None
    lib = (SCRIPTS / "lib.sh").read_text(encoding="utf-8")
    session = re.search(r'SOS_APP_SESSION_SECONDS="\$\{SOS_APP_SESSION_SECONDS:-(\d+)\}"', lib)
    assert session is not None
    # botocore refreshes 15 minutes before expiry: a file must always have more than that left.
    assert int(session.group(1)) - int(m.group(1)) * 60 > 15 * 60
    service = (SYSTEMD / "schoolos-app-credentials.service").read_text(encoding="utf-8")
    assert "ExecStart=@INSTALL_DIR@/scripts/app-credentials.sh" in service
    upgrade = (SCRIPTS / "upgrade.sh").read_text(encoding="utf-8").splitlines()
    code = [ln.strip() for ln in upgrade if ln.strip() and not ln.strip().startswith("#")]
    trap = code.index("trap rollback ERR")
    units = code.index('install_units "$(active_release_dir)"')
    refresh = code.index("refresh_app_credentials")
    restart = next(i for i, ln in enumerate(code) if ln.startswith("for svc in "))
    assert trap < units < refresh < restart
    bootstrap = (SCRIPTS / "bootstrap-host.sh").read_text(encoding="utf-8").splitlines()
    first = [ln.strip() for ln in bootstrap if ln.strip() and not ln.startswith((" ", "#"))]
    assert first.index('install_units "$release_dir"') < first.index("start_stack")
    assert any(ln.startswith("refresh_app_credentials") for ln in first)


def _bash() -> str | None:
    if sys.platform == "win32":
        git = shutil.which("git")
        for parent in Path(git).resolve().parents if git else ():
            candidate = parent / "bin" / "bash.exe"
            if candidate.exists():
                return str(candidate)
        return None
    return shutil.which("bash")


def _bash_path(path: Path) -> str:
    """A PATH entry for bash (Git for Windows: ``D:/x`` -> ``/d/x``; the colon would split it)."""
    posix = path.as_posix()
    if sys.platform == "win32" and re.match(r"^[A-Za-z]:/", posix):
        return f"/{posix[0].lower()}{posix[2:]}"
    return posix


FAKE_AWS = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "sts get-caller-identity") echo 111122223333 ;;
  "sts assume-role")
    role=""; while (($#)); do [[ $1 == --role-arn ]] && role="$2"; shift; done
    name="${role##*-}"
    printf '{"Credentials":{"AccessKeyId":"ASIASYNTHETIC%s",'\
'"SecretAccessKey":"synthetic-secret-%s",'\
'"SessionToken":"synthetic-token-%s","Expiration":"2026-10-05T12:00:00+00:00"}}\n' \
      "${name^^}" "$name" "$name" ;;
  "configure export-credentials")
    printf '{"Version":1,"AccessKeyId":"ASIASYNTHETICHOST","SecretAccessKey":"synthetic-host",'\
'"SessionToken":"synthetic-host-token","Expiration":"2026-10-05T12:00:00+00:00"}\n' ;;
  *) exit 2 ;;
esac
"""


def _refresh(tmp_path: Path, walg: bool, fake: str = FAKE_AWS) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    if bash is None or shutil.which("jq") is None:
        pytest.skip("bash and jq are needed")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_aws = bin_dir / "aws"
    fake_aws.write_text(fake, encoding="utf-8", newline="\n")
    fake_aws.chmod(0o755)
    ids = subprocess.run(
        [bash, "-c", "echo $(id -u) $(id -g)"], capture_output=True, text=True, check=True
    ).stdout.split()
    env = {
        **os.environ,
        "SOS_DATA_DIR": (tmp_path / "data").as_posix(),
        "SOS_AWS_OWNER": ids[0],
        "SOS_APP_GID": ids[1],
        "SOS_DB_GID": ids[1],
        "SOS_LIB": (SCRIPTS / "lib.sh").as_posix(),
        "FAKE_LOG": (tmp_path / "aws.log").as_posix(),
        "FAKE_BIN": _bash_path(bin_dir),
        "SCHOOL_CODE": "demo-school",
        "AWS_REGION": "ap-south-1",
        "SOS_WALG_ENABLED": "true" if walg else "false",
    }
    (tmp_path / "data").mkdir()
    # lib.sh pins PATH for hosts; put the fake aws first and keep the caller's PATH for jq.
    script = 'p="$PATH"; . "$SOS_LIB"; PATH="$FAKE_BIN:$p"; refresh_app_credentials'
    return subprocess.run(
        [bash, "-c", script], env=env, capture_output=True, text=True, timeout=60, check=False
    )


def test_W3_06_refresh_writes_each_container_its_own_role(tmp_path: Path) -> None:
    result = _refresh(tmp_path, walg=True)
    assert result.returncode == 0, result.stderr
    aws = tmp_path / "data" / "aws"
    log = (tmp_path / "aws.log").read_text(encoding="utf-8")
    assert "--role-arn arn:aws:iam::111122223333:role/sos-ded-demo-school-api" in log
    assert "--role-arn arn:aws:iam::111122223333:role/sos-ded-demo-school-worker" in log
    for name in ("api", "worker"):
        creds = json.loads((aws / name / "credentials.json").read_text(encoding="utf-8"))
        assert creds["Version"] == 1
        assert creds["AccessKeyId"] == f"ASIASYNTHETIC{name.upper()}"
        assert set(creds) == {
            "Version",
            "AccessKeyId",
            "SecretAccessKey",
            "SessionToken",
            "Expiration",
        }
        config = (aws / name / "config").read_text(encoding="utf-8")
        assert config == "[default]\ncredential_process = /bin/cat /run/aws/credentials.json\n"
    walg = (aws / "walg" / "credentials").read_text(encoding="utf-8")
    assert "aws_access_key_id = ASIASYNTHETICHOST" in walg
    # Never logged.
    assert "synthetic-secret" not in result.stderr + result.stdout
    assert "synthetic-token" not in result.stderr + result.stdout


def test_W3_06_refresh_fails_when_a_role_cannot_be_assumed(tmp_path: Path) -> None:
    refusing = FAKE_AWS.replace('"sts assume-role")', '"sts assume-role") exit 254 ;;\n  "x y")')
    result = _refresh(tmp_path, walg=False, fake=refusing)
    assert result.returncode == 1
    assert "cannot assume the api role" in result.stderr
    assert not (tmp_path / "data" / "aws" / "api" / "credentials.json").exists()
