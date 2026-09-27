"""Quality gates read from `evals/gates.toml` (docs/06 §13.2)."""

from __future__ import annotations

import operator
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from sos_evals.datasets import EVALS_DIR
from sos_evals.runner import Metrics

GATES_FILE = EVALS_DIR / "gates.toml"

Op = Literal["==", ">=", "<="]
_OPS: dict[str, Callable[[float, float], bool]] = {
    "==": operator.eq,
    ">=": operator.ge,
    "<=": operator.le,
}

EXIT_OK = 0
EXIT_HARD_FAIL = 1
EXIT_SOFT_FAIL = 2


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Gate(_Model):
    metric: str
    op: Op
    threshold: float
    severity: Literal["hard", "soft"]
    requirements: tuple[str, ...] = ()


class GateResult(_Model):
    gate: Gate
    value: float | None
    passed: bool


def load_gates(path: Path = GATES_FILE) -> tuple[Gate, ...]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError(f"{path.name}: unsupported version {data.get('version')!r}")
    gates = tuple(Gate.model_validate(row) for row in data.get("gate", []))
    unknown = [g.metric for g in gates if g.metric not in Metrics.model_fields]
    if unknown:
        raise ValueError(f"{path.name}: unknown metrics {unknown}")
    return gates


def evaluate(gates: Sequence[Gate], values: Metrics) -> tuple[GateResult, ...]:
    results = []
    for gate in gates:
        value = getattr(values, gate.metric)
        passed = value is not None and _OPS[gate.op](float(value), gate.threshold)
        results.append(GateResult(gate=gate, value=value, passed=passed))
    return tuple(results)


def exit_code(results: Sequence[GateResult], *, fail_on_soft: bool) -> int:
    if any(not r.passed and r.gate.severity == "hard" for r in results):
        return EXIT_HARD_FAIL
    if fail_on_soft and any(not r.passed for r in results):
        return EXIT_SOFT_FAIL
    return EXIT_OK
