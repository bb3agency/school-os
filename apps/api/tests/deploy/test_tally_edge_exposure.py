"""D61 (ADR-0032; docs/10 §13): the Tally edge agent's routes outside the app.

The edge agent calls ``/api/v1/edge/tally/*`` on the API directly (device-signed; the web/BFF
refuses that path). The shared ALB and the dedicated Caddyfile each route exactly that prefix to
the API, with a per-IP limit in front and the agent's 1 MiB body limit; nothing else of the API is
opened. The Terraform behaviour is asserted by ``terraform test`` in
``infra/terraform/modules/alb_waf/tests``; this file pins the text so a run without Terraform still
catches a wider rule. Synthetic values only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.tally import agent_auth
from app.tally.api import agent_router

REPO = Path(__file__).resolve().parents[4]
CADDYFILE = REPO / "deploy" / "dedicated" / "Caddyfile"
COMPOSE = REPO / "deploy" / "dedicated" / "compose.yaml"
ALB = REPO / "infra" / "terraform" / "modules" / "alb_waf" / "main.tf"
ALB_VARS = REPO / "infra" / "terraform" / "modules" / "alb_waf" / "variables.tf"
RATE_LIMITS = REPO / "apps" / "api" / "app" / "core" / "rate_limits.yaml"
PREFIX = "/api/v1/edge/tally"


def _block(text: str, header: str) -> str:
    """The ``{...}`` body that follows ``header`` (balanced braces)."""
    start = text.index("{", text.index(header) + len(header.rstrip("{ ")))
    depth = 0
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start + 1 : i]
    raise AssertionError(f"unbalanced block after {header!r}")


def _caddy_site() -> str:
    text = CADDYFILE.read_text(encoding="utf-8")
    return _block(text, "{$SOS_PUBLIC_HOST} {$SOS_CUSTOM_DOMAIN}")


def _top_level_lines(block: str) -> list[str]:
    """Lines of ``block`` at brace depth 0 (directives of the site itself)."""
    lines, depth = [], 0
    for raw in block.splitlines():
        line = raw.strip()
        if depth == 0 and line and not line.startswith("#"):
            lines.append(line)
        depth += line.count("{") - line.count("}")
    return lines


def _agent_methods() -> set[str]:
    methods: set[str] = set()
    for route in agent_router.routes:
        methods |= set(getattr(route, "methods", set()))
    return methods


# --- dedicated host: Caddyfile -----------------------------------------------------------------


def test_tally_edge_caddy_routes_only_the_agent_prefix_to_the_api() -> None:
    site = _caddy_site()
    matcher = _block(site, "@tally_edge {")
    paths = re.findall(r"(?m)^\s*path\s+(.+)$", matcher)
    assert paths == [f"{PREFIX}/*"], paths
    methods = set(re.search(r"(?m)^\s*method\s+(.+)$", matcher).group(1).split())  # type: ignore[union-attr]
    assert methods == _agent_methods() == {"GET", "POST", "PUT"}

    edge = _block(site, "handle @tally_edge {")
    assert re.findall(r"reverse_proxy\s+(\S+)", edge) == ["api:8000"]
    assert "-X-Service-Token" in edge  # the BFF's service token never rides along


def test_tally_edge_caddy_proxies_nothing_else_of_the_api() -> None:
    site = _caddy_site()
    # Only two upstreams in the whole file, and the api only inside the edge handle.
    upstreams = re.findall(r"reverse_proxy\s+(\S+)", site)
    assert sorted(upstreams) == ["api:8000", "web:3000"], upstreams
    top = _top_level_lines(site)
    assert not any(line.startswith("reverse_proxy") for line in top), "proxy outside a handle"
    handles = [line for line in top if line.startswith("handle")]
    assert handles == ["handle @tally_edge {", "handle {"], handles
    # No other matcher names the API.
    api_matchers = re.findall(r"(?m)^\s*(?:path|path_regexp)\s+[^\n]*?/api/v1[^\n]*$", site)
    assert all("/api/v1/edge/tally/*" in m or "/api/v1/fleet" in m for m in api_matchers), (
        api_matchers
    )


def test_tally_edge_caddy_keeps_fleet_and_metrics_hidden() -> None:
    web = _block(_caddy_site(), "handle {")
    assert re.search(r"@internal path /api/v1/fleet /api/v1/fleet/\* /metrics /metrics/\*", web)
    assert 'respond @internal "Not found" 404' in web


def test_tally_edge_caddy_body_limit_matches_the_agent() -> None:
    edge = _block(_caddy_site(), "handle @tally_edge {")
    size = re.search(r"request_body\s*\{\s*max_size\s+(\S+)\s*\}", edge)
    assert size, "no request_body limit on the edge routes"
    assert size.group(1) == "1MiB"  # 1048576 bytes, binary unit
    assert agent_auth.MAX_BODY_BYTES == 1024 * 1024
    web = _block(_caddy_site(), "handle {")
    assert re.search(r"request_body\s*\{\s*max_size\s+25MB\s*\}", web)


def test_tally_edge_caddy_has_no_rate_limit_plugin_so_the_api_limits_per_ip() -> None:
    """The stock Caddy image has no rate limiter; the API's fail-closed per-IP layer covers the
    machine paths, and Caddy sets X-Forwarded-For itself so that layer sees the client."""
    text = CADDYFILE.read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*rate_limit\b", text)
    config = yaml.safe_load(RATE_LIMITS.read_text(encoding="utf-8"))
    assert any(f"{PREFIX}/".startswith(p) for p in config["machine_path_prefixes"])
    assert not re.search(r"(?m)^\s*trusted_proxies\b", text)


# --- dedicated host: compose networks ----------------------------------------------------------


def _compose() -> dict[str, Any]:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_tally_edge_compose_joins_only_caddy_and_api() -> None:
    compose = _compose()
    members = {
        name for name, svc in compose["services"].items() if "edge_api" in svc.get("networks", [])
    }
    assert members == {"caddy", "api"}
    assert compose["networks"]["edge_api"]["internal"] is True
    # The api is still not published on the host.
    assert "ports" not in compose["services"]["api"]


# --- shared tier: ALB listener rule and WAF (text pins; terraform test asserts the plan) ---------


def _resource(text: str, kind: str, name: str) -> str:
    return _block(text, f'resource "{kind}" "{name}"')


def test_tally_edge_alb_rule_routes_only_the_agent_prefix() -> None:
    text = ALB.read_text(encoding="utf-8")
    rule = _resource(text, "aws_lb_listener_rule", "tally_edge")
    assert "count = var.expose_tally_edge ? 1 : 0" in rule
    paths = re.findall(r"values\s*=\s*\[([^\]]*)\]", rule)
    assert paths[0].strip() == f'"{PREFIX}/*"'
    assert {m.strip('" ') for m in paths[1].split(",")} == _agent_methods()
    assert "aws_lb_target_group.api[0].arn" in rule
    # The only path patterns that forward to the API.
    assert sorted(re.findall(r'path_pattern\s*\{\s*values\s*=\s*\[\s*"([^"]+)"', text)) == [
        "/api/v1/edge/tally/*",
        "/api/v1/fleet/heartbeat",
    ]
    https = _resource(text, "aws_lb_listener", "https")
    assert "aws_lb_target_group.web.arn" in https  # default stays the web/BFF


def test_tally_edge_alb_is_behind_the_per_ip_machine_rate_rule() -> None:
    variables = ALB_VARS.read_text(encoding="utf-8")
    prefixes = _block(variables, 'variable "waf_machine_path_prefixes"')
    default = re.search(r"default\s*=\s*\[([^\]]*)\]", prefixes)
    assert default
    values = [v.strip().strip('"') for v in default.group(1).split(",")]
    assert any(f"{PREFIX}/".startswith(v) for v in values), values
    rule = _resource(ALB.read_text(encoding="utf-8"), "aws_lb_listener_rule", "tally_edge")
    assert "precondition" in rule
    assert "waf_machine_path_prefixes" in rule


def test_tally_edge_exposed_in_the_shared_environments() -> None:
    for env in ("staging", "prod"):
        main = (REPO / "infra" / "terraform" / "envs" / env / "main.tf").read_text(encoding="utf-8")
        assert re.search(r"(?m)^\s*expose_tally_edge\s*=\s*true\s*$", main), env
