"""One Valkey user per service, and the web/BFF only on its own keys (audit 2026-10-05 P2-06).

web/BFF, api, worker and beat used to share one Valkey credential with no ACL. Code execution in
the internet-facing BFF could then ``LPUSH`` Celery messages (any task name, run by the worker
role), rewrite authz snapshot cache entries, rate limits and AI budget counters. Now:

- dedicated hosts (``deploy/dedicated/compose.yaml``): Valkey ACL users ``web``, ``api``,
  ``worker``, ``beat`` and ``health`` (healthcheck, PING only); the ``default`` user is off. Each
  password is derived on the host from the generated ``VALKEY_PASSWORD``
  (``scripts/lib.sh valkey_user_passwords``), so one never reveals another;
- shared tier (``infra/terraform/modules/redis``): ElastiCache RBAC users with the same access
  strings, one connection secret per service.

The web user may touch only ``sos:web:*`` (sessions) and ``sos:rl:v1:bff:*`` (sign-in rate
limits), with read/write commands, its two Lua scripts (``EVAL``, keys declared) and no dangerous
command, ``SELECT`` or pub/sub. The live tests apply the compose rules to a real Valkey (the
dedicated image) and replay what the BFF and Celery actually do. Synthetic values only.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import redis
import yaml
from tests.deploy.test_release_pins import _bash

REPO = Path(__file__).resolve().parents[4]
DEDICATED = REPO / "deploy" / "dedicated"
COMPOSE = DEDICATED / "compose.yaml"
LIB = DEDICATED / "scripts" / "lib.sh"
REDIS_MODULE = REPO / "infra" / "terraform" / "modules" / "redis" / "main.tf"
SHARED = REPO / "infra" / "terraform" / "modules" / "shared_platform" / "main.tf"
BFF_REDIS = REPO / "apps" / "web" / "src" / "server" / "session" / "redis.ts"
VALKEY_IMAGE = "valkey/valkey:8.1.10-alpine"  # the deploy/dedicated/compose.yaml image

SERVICE_USERS = ("web", "api", "worker", "beat")
ACL_LINE = re.compile(r"""["']user (\w+) (on|off)(?: >\$\$(VALKEY_\w+_PASSWORD))? ([^"']*)["']""")


def _services() -> dict[str, Any]:
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = doc["services"]
    assert isinstance(services, dict)
    return services


def compose_acl() -> dict[str, tuple[str, str | None, str]]:
    """user -> (on/off, password variable, rules) from the valkey service's start command."""
    command = " ".join(_services()["valkey"]["command"])
    users = {
        m.group(1): (m.group(2), m.group(3), m.group(4).strip()) for m in ACL_LINE.finditer(command)
    }
    assert users, "the valkey service writes no ACL users"
    return users


def terraform_access_strings() -> dict[str, str]:
    text = REDIS_MODULE.read_text(encoding="utf-8")
    m = re.search(r"(?ms)^\s*access_strings\s*=\s*\{(.*?)^\s*\}", text)
    assert m, "modules/redis has no access_strings map"
    return dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', m.group(1)))


def _env(service: str) -> dict[str, str]:
    env = _services()[service].get("environment", {})
    return {str(k): str(v) for k, v in env.items()}


# --- dedicated: compose ------------------------------------------------------------------------


def test_P2_06_default_user_is_off_and_every_service_has_its_own_user() -> None:
    acl = compose_acl()
    assert acl["default"][0] == "off"
    assert "-@all" in acl["default"][2].split()
    for user in (*SERVICE_USERS, "health"):
        state, password, _ = acl[user]
        assert state == "on"
        assert password == f"VALKEY_{user.upper()}_PASSWORD"
    passwords = [acl[u][1] for u in (*SERVICE_USERS, "health")]
    assert len(set(passwords)) == len(passwords)


def test_P2_06_web_reaches_only_its_own_keys_without_dangerous_commands() -> None:
    rules = compose_acl()["web"][2].split()
    keys = {r for r in rules if r.startswith("~")}
    assert keys == {"~sos:web:*", "~sos:rl:v1:bff:*"}
    assert not [
        r for r in rules if r.startswith("&") or r in ("allchannels", "allkeys", "allcommands")
    ]
    assert rules.index("-@all") < rules.index("+@read")
    assert "-@dangerous" in rules
    assert rules.index("-@dangerous") > rules.index("+@write")
    assert not {"+@all", "+select", "+@scripting", "+@admin", "+@pubsub"} & set(rules)


def test_P2_06_health_user_can_only_ping() -> None:
    assert compose_acl()["health"][2].split() == ["-@all", "+ping"]


@pytest.mark.parametrize(
    ("service", "user", "variable"),
    [
        ("web", "web", "REDIS_URL"),
        ("api", "api", "SOS_REDIS_URL"),
        ("worker", "worker", "SOS_REDIS_URL"),
        ("beat", "beat", "SOS_REDIS_URL"),
    ],
)
def test_P2_06_each_service_connects_as_its_own_user(
    service: str, user: str, variable: str
) -> None:
    env = _env(service)
    url = env[variable]
    assert url == f"redis://{user}:${{VALKEY_{user.upper()}_PASSWORD:?}}@valkey:6379/0"
    others = {f"VALKEY_{u.upper()}_PASSWORD" for u in (*SERVICE_USERS, "health") if u != user}
    others.add("VALKEY_PASSWORD")
    leaked = {name for name in others for value in env.values() if name in value}
    assert not leaked, f"{service} receives another user's Valkey password: {leaked}"


def test_P2_06_no_container_but_valkey_gets_any_valkey_password() -> None:
    for name, service in _services().items():
        if name in ("valkey", *SERVICE_USERS):
            continue
        text = yaml.safe_dump(service.get("environment", {}))
        assert "VALKEY_" not in text, f"{name} receives a Valkey password"
    valkey_env = _env("valkey")
    assert "VALKEY_PASSWORD" not in valkey_env, "the master secret never enters a container"


def test_P2_06_valkey_command_line_holds_no_password() -> None:
    """Passwords go into a tmpfs ACL file; argv is visible in ``ps`` (as before for requirepass)."""
    command = " ".join(_services()["valkey"]["command"])
    exec_part = command[command.index("exec valkey-server") :]
    assert "PASSWORD" not in exec_part
    assert "--aclfile /tmp/users.acl" in exec_part
    assert "requirepass" not in command


# --- shared tier: the same access strings in ElastiCache RBAC ------------------------------------


def test_P2_06_shared_tier_uses_the_same_rules_as_dedicated_hosts() -> None:
    acl = compose_acl()
    tf = terraform_access_strings()
    assert set(tf) == {"default", *SERVICE_USERS}
    assert tf["default"].split()[0] == "off"
    for user in SERVICE_USERS:
        assert tf[user] == f"on {acl[user][2]}", f"{user}: Terraform and compose rules differ"


@pytest.mark.parametrize(
    ("module", "user"),
    [
        ("web", "web"),
        ("api", "api"),
        ("worker", "worker"),
        ("worker_pdf", "worker"),
        ("beat", "beat"),
    ],
)
def test_P2_06_shared_tier_tasks_get_only_their_own_connection_secret(
    module: str, user: str
) -> None:
    from tests.deploy.test_env_contract import shared_hcl

    hcl = shared_hcl()
    users = dict(re.findall(r'(\w+)\s*=\s*"(\w+)"', hcl.locals["redis_secret"].split("}")[0]))
    attrs = hcl.block("module", module)
    variable = "REDIS_URL" if module == "web" else "SOS_REDIS_URL"
    assert hcl.map_entries(attrs["secrets"])[variable] == f'local.redis_secret["{module}"].url'
    assert users[module] == user
    assert re.findall(r'local\.redis_secret\["(\w+)"\]\.arn', attrs["secret_arns"]) == [module]
    assert "module.redis.secret_arn" not in hcl.text, "the single shared credential is gone"


# --- live: the rules on a real Valkey -------------------------------------------------------------


def _bff_scripts() -> list[str]:
    text = BFF_REDIS.read_text(encoding="utf-8")
    scripts = re.findall(r"const [A-Z_]+ = `([^`]*redis\.call[^`]*)`;", text)
    assert len(scripts) == 2, "the BFF's Lua scripts changed; update this test"
    return scripts


@pytest.fixture(scope="module")
def valkey() -> Iterator[tuple[str, int]]:
    from testcontainers.community.valkey import ValkeyContainer

    with ValkeyContainer(VALKEY_IMAGE) as container:
        host, port = container.get_container_host_ip(), int(container.get_exposed_port())
        admin: Any = redis.Redis(host=host, port=port, socket_timeout=5)
        acl = compose_acl()
        for user, (_state, _password, rules) in acl.items():
            if user == "default":
                continue
            admin.execute_command(
                "ACL", "SETUSER", user, "reset", "on", f">{user}-synthetic-pw", *rules.split()
            )
        # Last: this connection loses its rights with the default user.
        admin.execute_command(
            "ACL", "SETUSER", "default", "reset", *acl["default"][2].split(), "off"
        )
        admin.close()
        yield host, port


def _client(valkey: tuple[str, int], user: str) -> Any:  # execute_command is untyped
    host, port = valkey
    return redis.Redis(
        host=host, port=port, username=user, password=f"{user}-synthetic-pw", socket_timeout=5
    )


def test_P2_06_the_bff_keeps_working_as_web(valkey: tuple[str, int]) -> None:
    web = _client(valkey, "web")
    del_if_equals, incr_window = _bff_scripts()
    session = "sos:web:sess:0192a0de-0000-7000-8000-000000000001"
    assert web.set(session, "meta", px=60_000, nx=True)
    assert web.get(session) == b"meta"
    assert web.pexpire(session, 30_000)
    assert web.sadd("sos:web:sess:idx:tenant:abc", "s1") == 1
    assert web.smembers("sos:web:sess:idx:tenant:abc") == {b"s1"}
    assert web.srem("sos:web:sess:idx:tenant:abc", "s1") == 1
    assert web.eval(del_if_equals, 1, session, "meta") == 1
    assert web.eval(incr_window, 1, "sos:rl:v1:bff:auth:abc", "60000")[0] == 1
    assert web.delete("sos:rl:v1:bff:auth:abc") == 1
    assert web.ping()


@pytest.mark.parametrize(
    "attempt",
    [
        ("LPUSH", "maintenance", "{}"),  # a Celery message on any queue
        ("GET", "sos:authz:snap:t:1:m"),  # authz snapshot cache
        ("SET", "sos:rl:v1:ip:abc", "0"),  # the API's rate limits
        ("SET", "sos:kb:spend:t:2026-10", "0"),  # AI budget counters
        ("SELECT", "1"),
        ("KEYS", "*"),
        ("FLUSHDB",),
        ("CONFIG", "GET", "*"),
        ("PUBLISH", "celery.pidbox", "x"),
        ("EVAL", "return redis.call('LPUSH', 'maintenance', 'x')", "0"),
        ("EVAL", "return redis.call('GET', KEYS[1])", "1", "sos:authz:gen:t"),
    ],
)
def test_P2_06_web_cannot_reach_the_broker_or_api_keys(
    valkey: tuple[str, int], attempt: tuple[str, ...]
) -> None:
    web = _client(valkey, "web")
    with pytest.raises((redis.exceptions.NoPermissionError, redis.exceptions.ResponseError)) as err:
        web.execute_command(*attempt)
    assert "perm" in str(err.value).lower() or "acl" in str(err.value).lower()


def test_P2_06_default_user_is_disabled(valkey: tuple[str, int]) -> None:
    host, port = valkey
    anonymous = redis.Redis(host=host, port=port, socket_timeout=5)
    with pytest.raises(redis.exceptions.RedisError):
        anonymous.ping()


def test_P2_06_celery_runs_as_api_worker_and_beat(valkey: tuple[str, int]) -> None:
    """kombu publishes as api and beat and consumes as worker; dangerous commands stay refused."""
    from kombu import Connection

    host, port = valkey
    url = "redis://{0}:{0}-synthetic-pw@" + f"{host}:{port}/0"
    for sender in ("api", "beat"):
        with Connection(url.format(sender)) as conn:
            conn.SimpleQueue("maintenance").put({"task": "synthetic.ping", "from": sender})
        with Connection(url.format("worker")) as conn:
            queue = conn.SimpleQueue("maintenance")
            message = queue.get(timeout=5)
            assert message.payload == {"task": "synthetic.ping", "from": sender}
            message.ack()
            queue.close()
    api = _client(valkey, "api")
    for attempt in (("FLUSHALL",), ("CONFIG", "SET", "maxmemory", "1"), ("ACL", "LIST")):
        with pytest.raises(redis.exceptions.NoPermissionError):
            api.execute_command(*attempt)


# --- host: per-user passwords derived from the generated secret ---------------------------------


def test_P2_06_render_derives_distinct_per_user_passwords(tmp_path: Path) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not installed")
    secrets = tmp_path / "secrets.env"
    secrets.write_text("VALKEY_PASSWORD='synthetic-valkey-master-0123456789'\n", newline="\n")
    harness = (
        f'. "{LIB.as_posix()}"\n'
        f"SOS_SECRETS_ENV={shlex.quote(secrets.as_posix())}\n"
        "valkey_user_passwords\n"
    )
    first = subprocess.run(
        [bash, "-c", harness], capture_output=True, text=True, timeout=60, check=True
    )
    again = subprocess.run(
        [bash, "-c", harness], capture_output=True, text=True, timeout=60, check=True
    )
    assert first.stdout == again.stdout, "derivation is deterministic (no rotation on every render)"
    values = dict(line.split("=", 1) for line in first.stdout.splitlines())
    assert set(values) == {f"VALKEY_{u.upper()}_PASSWORD" for u in (*SERVICE_USERS, "health")}
    from tests.deploy.test_env_contract import template_derived_names

    assert set(values) == template_derived_names(), "deploy/dedicated/.env.template lists them"
    passwords = {v.strip("'") for v in values.values()}
    assert len(passwords) == len(values)
    assert all(re.fullmatch(r"[0-9a-f]{64}", p) for p in passwords)
    assert "synthetic-valkey-master" not in first.stdout

    secrets.write_text("SOS_SYNTHETIC=1\n", newline="\n")
    missing = subprocess.run(
        [bash, "-c", harness], capture_output=True, text=True, timeout=60, check=False
    )
    assert missing.returncode != 0
