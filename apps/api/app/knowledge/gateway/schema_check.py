"""Validate structured model output against the caller's JSON schema (strict subset).

``generate_json`` promises output "validated against ``schema``" (interfaces.LlmGateway). The
provider's structured outputs already constrain decoding; this is the server-side check that
does not trust it. Only the keywords the knowledge schemas use are supported, and an unknown
keyword is an error (fail closed rather than silently skip a constraint): ``type`` (one or a
list), ``properties``, ``required``, ``additionalProperties`` (``false`` or a schema), ``items``,
``minItems``, ``maxItems``, ``enum``, ``const``, ``anyOf``, ``minimum``, ``maximum``,
``minLength``, ``maxLength``, plus the annotations ``description``, ``title`` and ``format``.
Errors name the JSON path only, never the value (it may be personal data).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_ANNOTATIONS = frozenset({"description", "title", "format", "$schema"})
_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "enum",
        "const",
        "anyOf",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
    }
)


class SchemaViolation(ValueError):
    """``path`` (e.g. ``$.rows[2].dob``) and what failed; never the offending value."""


def _is_type(value: Any, name: str) -> bool:  # noqa: PLR0911 - one return per JSON type
    match name:
        case "object":
            return isinstance(value, Mapping)
        case "array":
            return isinstance(value, list)
        case "string":
            return isinstance(value, str)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        case "boolean":
            return isinstance(value, bool)
        case "null":
            return value is None
    raise SchemaViolation(f"unsupported type {name!r}")


def validate(  # noqa: PLR0912 - one branch per supported keyword, kept in one place
    value: Any, schema: Mapping[str, Any], path: str = "$"
) -> None:
    unknown = set(schema) - _KEYWORDS - _ANNOTATIONS
    if unknown:
        raise SchemaViolation(f"{path}: unsupported schema keywords {sorted(unknown)}")
    if "anyOf" in schema:
        options: Sequence[Mapping[str, Any]] = schema["anyOf"]
        for option in options:
            try:
                validate(value, option, path)
                break
            except SchemaViolation:
                continue
        else:
            raise SchemaViolation(f"{path}: matches no anyOf option")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, t) for t in types):
            raise SchemaViolation(f"{path}: expected {'/'.join(types)}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaViolation(f"{path}: not one of the allowed values")
    if "const" in schema and value != schema["const"]:
        raise SchemaViolation(f"{path}: not the constant value")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise SchemaViolation(f"{path}: too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaViolation(f"{path}: too long")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaViolation(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaViolation(f"{path}: above maximum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise SchemaViolation(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaViolation(f"{path}: too many items")
        if "items" in schema:
            for i, item in enumerate(value):
                validate(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, Mapping):
        properties: Mapping[str, Mapping[str, Any]] = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in value:
                raise SchemaViolation(f"{path}: missing {name}")
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                validate(item, properties[name], f"{path}.{name}")
            elif extra is False:
                raise SchemaViolation(f"{path}: unexpected property")
            elif isinstance(extra, Mapping):
                validate(item, extra, f"{path}.{name}")


def example(schema: Mapping[str, Any]) -> Any:  # noqa: PLR0911 - one return per JSON type
    """A deterministic minimal instance of ``schema`` (the offline fake's JSON output)."""
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return example(schema["anyOf"][0])
    types = schema.get("type", "null")
    kind = "null" if isinstance(types, list) and "null" in types else types
    if isinstance(kind, list):
        kind = kind[0]
    match kind:
        case "object":
            props: Mapping[str, Mapping[str, Any]] = schema.get("properties", {})
            return {
                name: example(props[name]) for name in schema.get("required", ()) if name in props
            }
        case "array":
            count = schema.get("minItems", 0)
            return [example(schema.get("items", {})) for _ in range(count)]
        case "string":
            return "x" * schema.get("minLength", 0)
        case "integer" | "number":
            return schema.get("minimum", 0)
        case "boolean":
            return False
    return None


__all__ = ["SchemaViolation", "example", "validate"]
