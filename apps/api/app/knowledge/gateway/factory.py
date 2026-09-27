"""Build the gateway for this process from settings (composition root helper; ADR-0005).

Provider mode guards (SEC-020, invariant 10), on top of the ``Settings`` start-up guards:

- ``fake`` is refused in staging/prod even if a ``Settings`` object was built around the guard;
- ``live`` needs ``Settings.anthropic_api_key`` (an organization key; never an environment
  variable read by the SDK, never a personal subscription);
- ``SOS_KB_ENABLED`` is read on every call (kill switch): off means :class:`AiDisabled`.

The per-school switch and budget come from ``policy`` (the composition root implements it with
``tenancy.service``; see :mod:`.budget`). Spend and rate-limit counters use Valkey in
staging/prod and process memory locally.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import redis

from app.authz.kv import KVStore, kv_store
from app.core.config import KnowledgeProviderMode, Settings
from app.knowledge.config.llm import LlmConfig, load_llm_config
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
from app.knowledge.gateway.gateway import Gateway
from app.knowledge.gateway.metering import MeteringSink
from app.knowledge.gateway.transport import Transport


class ProviderModeError(RuntimeError):
    """The configured provider mode is not allowed here (a deployment error, fail closed)."""


def build_transport(settings: Settings, config: LlmConfig) -> Transport:
    mode = settings.resolved_kb_provider_mode
    if mode is KnowledgeProviderMode.FAKE:
        if settings.is_production_like:
            raise ProviderModeError(f"the fake AI provider is not allowed in {settings.env}")
        return FakeTransport()
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value().strip():
        raise ProviderModeError("live AI needs SOS_ANTHROPIC_API_KEY (an organization API key)")
    return AnthropicTransport(api_key=key.get_secret_value(), base_url=config.client.api_base_url)


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
        transport=transport or build_transport(settings, config),
        guard=guard,
        sink=sink,
        enabled=enabled or (lambda: settings.kb_enabled),
    )


__all__ = ["ProviderModeError", "build_gateway", "build_spend_ledger", "build_transport"]
