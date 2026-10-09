"""Build the gateway for this process from settings (composition root helper; ADR-0005,
ADR-0033).

Provider mode guards (SEC-020, invariant 10), on top of the ``Settings`` start-up guards:

- ``fake`` is refused in staging/prod even if a ``Settings`` object was built around the guard;
  in local/CI every provider gets an offline stand-in in its own wire format (Gemini roles go
  through :class:`.fake_gemini.GeminiWireFake`, so the Gemini codec is what CI exercises);
- ``live`` builds a transport only for the providers ``models.yaml`` actually uses: Gemini needs
  ``SOS_LLM_GCP_PROJECT`` and service-identity credentials (never a person's login), and in
  staging/prod an India location and ``SOS_LLM_ZDR_CONFIRMED``; Anthropic (fallback) needs an
  organization API key and, in staging/prod, ``SOS_ANTHROPIC_ZDR_CONFIRMED`` (the Claude safety
  lock, :func:`require_provider_agreements`, also run at API and worker start-up). A missing
  requirement is a :class:`ProviderModeError` (fail closed).
- ``SOS_KB_ENABLED`` is read on every call (kill switch): off means :class:`AiDisabled`.

The per-school switch and budget come from ``policy`` (the composition root implements it with
``tenancy.service``; see :mod:`.budget`). Spend and rate-limit counters use Valkey in
staging/prod and process memory locally.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import redis

from app.authz.kv import KVStore, kv_store
from app.core.config import INDIA_GCP_LOCATIONS, KnowledgeProviderMode, Settings
from app.knowledge.config.llm import LlmConfig, Provider, load_llm_config
from app.knowledge.config.tools import ToolsConfig, load_tools_config
from app.knowledge.gateway.anthropic_transport import AnthropicTransport
from app.knowledge.gateway.budget import (
    BudgetGuard,
    InMemorySpendLedger,
    SpendLedger,
    TenantAiPolicy,
    ValkeySpendLedger,
)
from app.knowledge.gateway.fake import FakeTransport
from app.knowledge.gateway.fake_gemini import GeminiWireFake
from app.knowledge.gateway.gateway import Gateway
from app.knowledge.gateway.gemini_auth import GoogleTokenSource
from app.knowledge.gateway.gemini_cache import ContextCaches
from app.knowledge.gateway.gemini_transport import GeminiTransport
from app.knowledge.gateway.metering import MeteringSink
from app.knowledge.gateway.transport import Transport, wire_of


class ProviderModeError(RuntimeError):
    """The configured provider mode is not allowed here (a deployment error, fail closed)."""


def providers_in_use(config: LlmConfig) -> frozenset[Provider]:
    return frozenset(role.provider for role in config.roles.values())


def require_provider_agreements(settings: Settings, config: LlmConfig | None = None) -> None:
    """Claude safety lock (owner decision 2026-10-01; docs/08 §8, docs/10 §11): in staging/prod,
    no role may use provider anthropic unless ``SOS_ANTHROPIC_ZDR_CONFIRMED`` says the Anthropic
    Zero Data Retention agreement and DPA are in place. Local and CI are not checked (the fake
    provider and synthetic data only). Run at start-up by the API and the worker."""
    if not settings.is_production_like or settings.anthropic_zdr_confirmed:
        return
    roles = (config or load_llm_config()).anthropic_roles()
    if roles:
        raise ProviderModeError(
            f"roles {', '.join(roles)} use provider anthropic, which needs "
            f"SOS_ANTHROPIC_ZDR_CONFIRMED=true in {settings.env}. Set it only once the Anthropic "
            "Zero Data Retention agreement and DPA are signed (docs/10 §11), or switch those roles "
            "back to gemini in app/knowledge/config/models.yaml"
        )


def _anthropic(settings: Settings, config: LlmConfig) -> Transport:
    require_provider_agreements(settings, config)
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value().strip():
        raise ProviderModeError(
            "a role uses provider anthropic: live AI needs SOS_ANTHROPIC_API_KEY "
            "(an organization API key)"
        )
    return AnthropicTransport(api_key=key.get_secret_value(), base_url=config.client.api_base_url)


def _gemini(settings: Settings, config: LlmConfig) -> Transport:
    project = settings.llm_gcp_project
    raw = settings.llm_gcp_credentials_json
    source = settings.llm_gcp_credentials_source
    if not project:
        raise ProviderModeError("live AI on Gemini needs SOS_LLM_GCP_PROJECT")
    if source is None or raw is None or not raw.get_secret_value().strip():
        raise ProviderModeError(
            "live AI on Gemini needs SOS_LLM_GCP_CREDENTIALS_SOURCE and "
            "SOS_LLM_GCP_CREDENTIALS_JSON (a service identity)"
        )
    if settings.is_production_like:
        if settings.llm_gcp_location not in INDIA_GCP_LOCATIONS:
            raise ProviderModeError("SOS_LLM_GCP_LOCATION must be an India region")
        if not settings.llm_zdr_confirmed or not settings.llm_verify_cache_config:
            raise ProviderModeError("live AI needs the Vertex project set up for ZDR")
    http = httpx.Client(trust_env=False, follow_redirects=False)
    try:
        tokens = GoogleTokenSource(
            source, raw.get_secret_value(), http=http, aws_region=settings.aws_region
        )
    except ValueError as exc:
        http.close()
        raise ProviderModeError(str(exc)) from None
    caches = ContextCaches(
        config.gemini.explicit_cache,
        lambda model: (
            caps.explicit_cache_min_tokens
            if (caps := config.capabilities.get(model)) is not None
            else None
        ),
    )
    return GeminiTransport(
        project=project,
        location=settings.llm_gcp_location,
        config=config.gemini,
        tokens=tokens,
        caches=caches,
        http=http,
        verify_cache_config=settings.llm_verify_cache_config,
    )


def build_transports(settings: Settings, config: LlmConfig) -> dict[Provider, Transport]:
    """One transport per provider that a role of ``models.yaml`` uses."""
    mode = settings.resolved_kb_provider_mode
    used = providers_in_use(config)
    if mode is KnowledgeProviderMode.FAKE:
        if settings.is_production_like:
            raise ProviderModeError(f"the fake AI provider is not allowed in {settings.env}")
        fake = FakeTransport()
        return {"anthropic": fake, "gemini": GeminiWireFake(fake)}
    builders: dict[Provider, Callable[[Settings, LlmConfig], Transport]] = {
        "gemini": _gemini,
        "anthropic": _anthropic,
    }
    transports = {provider: builders[provider](settings, config) for provider in sorted(used)}
    for provider, transport in transports.items():
        if wire_of(transport) != provider:  # a live transport always speaks its own wire
            raise ProviderModeError(f"the {provider} transport speaks another wire format")
    return transports


def build_transport(settings: Settings, config: LlmConfig) -> Transport:
    """The transport that serves the ``answer`` role (for callers that need exactly one)."""
    return build_transports(settings, config)[config.roles["answer"].provider]


def build_spend_ledger(settings: Settings) -> SpendLedger:
    if settings.is_production_like:
        client: Any = redis.Redis.from_url(
            settings.redis_url.get_secret_value(), socket_timeout=0.5, socket_connect_timeout=0.5
        )
        return ValkeySpendLedger(client)
    return InMemorySpendLedger()


def build_gateway(
    settings: Settings,
    *,
    policy: TenantAiPolicy,
    sink: MeteringSink,
    transport: Transport | None = None,
    ledger: SpendLedger | None = None,
    counters: KVStore | None = None,
    config: LlmConfig | None = None,
    tools_config: ToolsConfig | None = None,
    enabled: Callable[[], bool] | None = None,
) -> Gateway:
    """``transport`` (tests, the eval bridge) serves every provider; otherwise one transport
    per provider in use is built from settings."""
    config = config or load_llm_config()
    guard = BudgetGuard(
        config,
        policy,
        ledger or build_spend_ledger(settings),
        counters or kv_store(),
    )
    return Gateway(
        config=config,
        tools_config=tools_config or load_tools_config(),
        transport=transport,
        transports=None if transport is not None else build_transports(settings, config),
        guard=guard,
        sink=sink,
        enabled=enabled or (lambda: settings.kb_enabled),
    )


__all__ = [
    "ProviderModeError",
    "build_gateway",
    "build_spend_ledger",
    "build_transport",
    "build_transports",
    "providers_in_use",
    "require_provider_agreements",
]
