"""``chunking.yaml`` extraction and token-estimate settings (invariant 13; docs/06 §4.2-4.5).

The shipped file keeps student records out of the index (identity evidence, raw import files,
C3) and the validator refuses a change that would let them in.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.knowledge.config.chunking import ChunkingConfig, load_chunking_config


def raw() -> dict[str, Any]:
    return load_chunking_config().model_dump(mode="json")


def test_PRV_shipped_config_never_indexes_student_records() -> None:
    extraction = load_chunking_config().extraction
    assert {"evidence", "import_file"} <= set(extraction.excluded_purposes)
    assert "C3" not in extraction.indexed_sensitivities


@pytest.mark.parametrize("purpose", ["evidence", "import_file"])
def test_PRV_removing_a_student_record_purpose_is_refused(purpose: str) -> None:
    data = raw()
    data["extraction"]["excluded_purposes"].remove(purpose)
    with pytest.raises(ValidationError, match=purpose):
        ChunkingConfig.model_validate(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("token_estimate", "latin_chars_per_token"), 0),
        (("language_dominant_share",), 0.5),
        (("extraction", "max_xml_bytes"), 10),
        (("extraction", "indexed_sensitivities"), ["C4"]),
        (("extraction", "unknown_key"), 1),
    ],
)
def test_invalid_extraction_settings_are_refused(path: tuple[str, ...], value: object) -> None:
    data = raw()
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValidationError):
        ChunkingConfig.model_validate(data)
