"""Versioned knowledge configuration (invariant 13; docs/06 §4.5, §4.6, §6, §7, §9, §12).

The shipped YAML files load and validate; the validators refuse the mistakes that would weaken a
control (more than 3 tool rounds, an unpriced model, an embeddings dimension that does not match
the column, a record tool outside the ADR-0008 whitelist or with an unknown permission).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any, get_args

import pytest
import yaml
from pydantic import ValidationError

from app.authz.catalog import permission_catalog
from app.knowledge.config import chunking, embeddings, llm, retrieval, tools
from app.knowledge.domain import ModelRole

LOADERS: dict[str, Callable[[], Any]] = {
    "models.yaml": llm.load_llm_config,
    "embeddings.yaml": embeddings.load_embeddings_config,
    "retrieval.yaml": retrieval.load_retrieval_config,
    "chunking.yaml": chunking.load_chunking_config,
    "tools.yaml": tools.load_tools_config,
}


def raw(name: str) -> dict[str, Any]:
    from app.knowledge.config._base import CONFIG_DIR

    data = yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize("name", sorted(LOADERS))
def test_shipped_config_files_load(name: str) -> None:
    config = LOADERS[name]()
    assert config.version >= 1


# --- models.yaml ---------------------------------------------------------------------------------


def test_FR_KB_003_every_model_role_is_configured() -> None:
    config = llm.load_llm_config()
    assert set(config.roles) == set(get_args(ModelRole))
    # ADR-0033 (2026-09-30, product owner): every role on Gemini; another provider needs an ADR.
    assert config.default_provider == "gemini"
    assert {r.provider for r in config.roles.values()} == {"gemini"}


def test_NFR_CST_001_every_configured_model_is_priced() -> None:
    config = llm.load_llm_config()
    for role in config.roles.values():
        assert role.model in config.prices


def test_NFR_CST_001_unpriced_model_is_refused() -> None:
    data = raw("models.yaml")
    data["roles"]["answer"]["model"] = "claude-unpriced-9"
    with pytest.raises(ValidationError, match="price"):
        llm.LlmConfig.model_validate(data)


def test_FR_KB_003_missing_role_is_refused() -> None:
    data = raw("models.yaml")
    del data["roles"]["metadata"]
    with pytest.raises(ValidationError, match="metadata"):
        llm.LlmConfig.model_validate(data)


@pytest.mark.parametrize("rounds", [0, 4])
def test_SEC_020_tool_rounds_are_capped_at_three(rounds: int) -> None:
    data = raw("models.yaml")
    data["limits"]["max_tool_rounds"] = rounds
    with pytest.raises(ValidationError, match="max_tool_rounds"):
        llm.LlmConfig.model_validate(data)


def test_SEC_020_tool_result_context_cannot_exceed_docs_budget() -> None:
    data = raw("models.yaml")
    data["limits"]["tool_result_context_tokens"] = 12_001
    with pytest.raises(ValidationError, match="tool_result_context_tokens"):
        llm.LlmConfig.model_validate(data)


def test_FR_KB_011_budget_alerts_before_it_degrades() -> None:
    config = llm.load_llm_config()
    assert 0 < config.budget.alert_fraction < config.budget.degrade_fraction <= 1
    data = raw("models.yaml")
    data["budget"]["alert_fraction"] = 1.0
    with pytest.raises(ValidationError, match="alert_fraction"):
        llm.LlmConfig.model_validate(data)


def test_eval_judge_is_offline_only_and_nothing_else_is() -> None:
    config = llm.load_llm_config()
    assert config.roles["eval_judge"].offline_only
    data = raw("models.yaml")
    data["roles"]["answer"]["offline_only"] = True
    with pytest.raises(ValidationError, match="offline_only"):
        llm.LlmConfig.model_validate(data)


@pytest.mark.parametrize("model", ["", "Claude Sonnet", "claude sonnet", "x"])
def test_model_ids_are_ids(model: str) -> None:
    data = raw("models.yaml")
    data["roles"]["answer"]["model"] = model
    with pytest.raises(ValidationError):
        llm.LlmConfig.model_validate(data)


def test_unknown_keys_are_refused() -> None:
    data = raw("models.yaml")
    data["temperature"] = 0.2
    with pytest.raises(ValidationError, match="temperature"):
        llm.LlmConfig.model_validate(data)


# --- embeddings.yaml -----------------------------------------------------------------------------


def test_FR_KB_001_embeddings_not_selected_until_adr_0006_evaluation() -> None:
    config = embeddings.load_embeddings_config()
    assert config.selected is None
    assert config.storage.dimensions == 1024  # docs/05 §6 placeholder halfvec(1024)
    assert config.storage.precision == "halfvec"
    assert config.query_cache_ttl_s == 600  # docs/06 §12: query embeddings cached 10 min


def test_FR_KB_001_selected_candidate_needs_a_model() -> None:
    data = raw("embeddings.yaml")
    key = next(iter(data["candidates"]))
    data["selected"] = key
    data["candidates"][key]["model"] = None
    with pytest.raises(ValidationError, match="model"):
        embeddings.EmbeddingsConfig.model_validate(data)


def test_FR_KB_001_selected_candidate_must_match_the_column_dimension() -> None:
    data = raw("embeddings.yaml")
    key = next(iter(data["candidates"]))
    data["selected"] = key
    data["candidates"][key]["model"] = "synthetic-embed-1"
    data["candidates"][key]["dimensions"] = 512
    with pytest.raises(ValidationError, match="re-embedding migration"):
        embeddings.EmbeddingsConfig.model_validate(data)


def test_FR_KB_001_selected_candidate_must_exist() -> None:
    data = raw("embeddings.yaml")
    data["selected"] = "no_such_candidate"
    with pytest.raises(ValidationError, match="candidates"):
        embeddings.EmbeddingsConfig.model_validate(data)


def test_FR_KB_001_a_complete_selection_validates() -> None:
    data = raw("embeddings.yaml")
    key = next(iter(data["candidates"]))
    data["selected"] = key
    data["candidates"][key]["model"] = "synthetic-embed-1"
    data["candidates"][key]["dimensions"] = 1024
    config = embeddings.EmbeddingsConfig.model_validate(data)
    assert config.selected_candidate is not None
    assert config.selected_candidate.model == "synthetic-embed-1"


# --- retrieval.yaml ------------------------------------------------------------------------------


def test_FR_KB_001_retrieval_matches_docs_06_section_6() -> None:
    config = retrieval.load_retrieval_config()
    assert config.fusion.method == "rrf"
    assert config.branches.full_text.ts_config == "simple"
    assert config.final_k <= config.branches.vector.limit
    assert config.rerank.enabled is False  # adopt only if evals show a gain
    assert config.rerank.provider == "off"
    assert config.contextual_chunks == "off"  # PO 2026-09-30: on only after evals show a gain
    assert config.rerank.voyage.model is None  # chosen by evaluation, never by default


def test_FR_KB_001_contextual_and_rerank_switches_accept_yaml_booleans_and_overrides() -> None:
    data = raw("retrieval.yaml")
    data["contextual_chunks"] = True  # a bare `on` in YAML 1.1
    data["rerank"]["provider"] = False  # a bare `off`
    config = retrieval.RetrievalConfig.model_validate(data)
    assert config.contextual
    assert not config.rerank.enabled
    switched = config.with_overrides(contextual_chunks="off", rerank="voyage")
    assert not switched.contextual
    assert switched.rerank.provider == "voyage"
    assert switched.rerank.candidates == config.rerank.candidates  # sizes stay from the file
    assert config.with_overrides() is config


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("rerank", "keep"), 60, "keep"),
        (("rerank", "candidates"), 500, "candidates"),
        (("rerank", "provider"), "cohere", "provider"),
        (("contextual_chunks",), "maybe", "contextual_chunks"),
    ],
)
def test_FR_KB_001_rerank_and_contextual_settings_are_validated(
    path: tuple[str, ...], value: object, match: str
) -> None:
    data = raw("retrieval.yaml")
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError, match=match):
        retrieval.RetrievalConfig.model_validate(data)


def test_FR_KB_001_contextual_yaml_is_consistent() -> None:
    from app.knowledge.config import contextual
    from app.knowledge.prompts.registry import load_prompt

    config = contextual.load_contextual_config()
    prompt = load_prompt(config.prompt.id, config.prompt.version)
    assert prompt.header.model_config_key == "contextualize"
    assert prompt.placeholders == {"title", "doc_type", "document"}
    assert config.prompt.label == "contextualize.v1"
    assert config.max_chars <= 1000  # kb.document_chunks.chunk_context CHECK


def test_FR_KB_001_final_k_cannot_exceed_candidate_lists() -> None:
    data = raw("retrieval.yaml")
    data["final_k"] = 500
    with pytest.raises(ValidationError, match="final_k"):
        retrieval.RetrievalConfig.model_validate(data)


# --- chunking.yaml -------------------------------------------------------------------------------


def test_chunk_sizes_are_consistent() -> None:
    config = chunking.load_chunking_config()
    assert config.overlap_tokens.max < config.target_tokens.min
    assert config.table_max_tokens >= config.target_tokens.max


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("target_tokens", "min"), 700, "min"),
        (("overlap_tokens", "max"), 400, "overlap"),
        (("table_max_tokens",), 100, "table_max_tokens"),
    ],
)
def test_inconsistent_chunk_sizes_are_refused(
    path: tuple[str, ...], value: int, match: str
) -> None:
    data = copy.deepcopy(raw("chunking.yaml"))
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValidationError, match=match):
        chunking.ChunkingConfig.model_validate(data)


# --- tools.yaml ----------------------------------------------------------------------------------


def test_FR_KB_004_tools_are_exactly_the_adr_0008_whitelist() -> None:
    config = tools.load_tools_config()
    assert set(config.tools) == tools.WHITELIST


def test_FR_KB_004_every_tool_permission_exists_in_the_catalog() -> None:
    catalog = permission_catalog()
    for name, tool in tools.load_tools_config().tools.items():
        for permission in tool.permissions():
            assert permission in catalog, f"{name}: unknown permission {permission}"
            assert not catalog[permission].is_platform, f"{name}: {permission} is platform"


def test_FR_KB_004_an_extra_tool_is_refused() -> None:
    data = raw("tools.yaml")
    data["tools"]["run_sql"] = {"permission": "student.read_basic"}
    with pytest.raises(ValidationError, match="whitelist"):
        tools.ToolsConfig.model_validate(data)


def test_SEC_018_record_tool_caps_match_docs_06_section_7() -> None:
    config = tools.load_tools_config()
    assert config.tools["find_students"].max_results == 20
    facts = config.tools["get_student_facts"]
    assert facts.max_fields == 8
    assert facts.sensitive_permission == "student.read_sensitive"
    assert config.tools["count_students"].small_cell_min == 5
