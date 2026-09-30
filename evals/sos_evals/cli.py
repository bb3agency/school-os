"""Command line: `python -m sos_evals run|generate` (docs/06 §13, `make eval`).

Exit codes of `run`: 0 all hard gates pass (and soft gates with --fail-on-soft); 1 a hard gate
failed; 2 only soft gates failed and --fail-on-soft was given; 3 the harness itself could not
run (bad dataset or configuration).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sos_evals import datasets, gates, generator, report, runner
from sos_evals.stubs import STUBS

EXIT_HARNESS_ERROR = 3
BASELINES_DIR = datasets.EVALS_DIR / "baselines"
REPORTS_DIR = datasets.EVALS_DIR / "reports"


def baseline_path(adapter: str, suite: str) -> Path:
    return BASELINES_DIR / f"{adapter}-{suite}.json"


def build_report(
    *,
    adapter: str,
    suite: datasets.Suite,
    datasets_dir: Path = datasets.DATASETS_DIR,
    gates_file: Path = gates.GATES_FILE,
    baseline_file: Path | None = None,
    fail_on_soft: bool = False,
) -> report.Report:
    data = datasets.load(datasets_dir)
    stub = STUBS[adapter](data.corpus, data.items)
    result = runner.run(
        data.select(suite),
        data.corpus,
        stub,
        stub,
        circular=stub,
        circular_cases=data.circulars,
        fee=stub,
        fee_cases=data.fees,
        ctx=stub,
        ctx_set=data.contextual,
        ctx_fast=suite == "fast",
        conversation=stub,
        conversation_cases=data.conversations,
        english=stub,
    )
    gate_results = gates.evaluate(gates.load_gates(gates_file), result.metrics)
    code = gates.exit_code(gate_results, fail_on_soft=fail_on_soft)
    base = report.load_baseline(baseline_file) if baseline_file else None
    return report.Report(
        adapter=adapter,
        suite=suite,
        dataset_sha256=data.sha256,
        passed_hard_gates=code != gates.EXIT_HARD_FAIL,
        exit_code=code,
        gates=gate_results,
        run=result,
        baseline_dataset_sha256=base.dataset_sha256 if base else None,
        diff=report.diff(base.metrics if base else None, result.metrics),
    )


def _run(args: argparse.Namespace) -> int:
    baseline_file = args.baseline or baseline_path(args.adapter, args.suite)
    result = build_report(
        adapter=args.adapter,
        suite=args.suite,
        datasets_dir=args.datasets,
        gates_file=args.gates,
        baseline_file=baseline_file,
        fail_on_soft=args.fail_on_soft,
    )
    json_path, md_path = report.write(result, args.out)
    for gate in result.gates:
        verdict = "pass" if gate.passed else "FAIL"
        g = gate.gate
        sys.stdout.write(
            f"{verdict:4}  {g.severity:4}  {g.metric} = {report.fmt(gate.value)} "
            f"({g.op} {report.fmt(g.threshold)})\n"
        )
    worse = [row.metric for row in result.diff if row.change == "worse"]
    if worse:
        sys.stdout.write(f"worse than baseline: {', '.join(worse)}\n")
    sys.stdout.write(f"report: {md_path} and {json_path}\n")
    if args.write_baseline:
        report.write_baseline(
            baseline_file,
            report.Baseline(
                adapter=result.adapter,
                suite=result.suite,
                dataset_sha256=result.dataset_sha256,
                metrics=result.run.metrics,
            ),
        )
        sys.stdout.write(f"baseline written: {baseline_file}\n")
    sys.stdout.write(
        f"eval {'PASSED' if result.exit_code == 0 else 'FAILED'} (exit {result.exit_code})\n"
    )
    return result.exit_code


def _generate(args: argparse.Namespace) -> int:
    if args.check:
        stale = generator.stale_files(args.out)
        if stale:
            sys.stderr.write(
                f"datasets are stale: {', '.join(stale)}; run `python -m sos_evals generate`\n"
            )
            return 1
        sys.stdout.write("datasets are up to date\n")
        return 0
    for path in generator.write(args.out):
        sys.stdout.write(f"wrote {path}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sos_evals", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run the question set and apply the gates")
    run.add_argument("--adapter", choices=sorted(STUBS), default="stub-perfect")
    run.add_argument("--suite", choices=["fast", "full"], default="fast")
    run.add_argument("--datasets", type=Path, default=datasets.DATASETS_DIR)
    run.add_argument("--gates", type=Path, default=gates.GATES_FILE)
    run.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="default: evals/baselines/<adapter>-<suite>.json",
    )
    run.add_argument("--out", type=Path, default=REPORTS_DIR)
    run.add_argument(
        "--fail-on-soft",
        action="store_true",
        help="exit 2 when a soft gate fails (release and nightly runs)",
    )
    run.add_argument(
        "--write-baseline", action="store_true", help="store this run's metrics as the new baseline"
    )
    run.set_defaults(handler=_run)

    gen = commands.add_parser("generate", help="write the synthetic datasets")
    gen.add_argument("--out", type=Path, default=datasets.DATASETS_DIR)
    gen.add_argument("--check", action="store_true", help="exit 1 when the files are stale")
    gen.set_defaults(handler=_generate)

    args = parser.parse_args(argv)
    try:
        code: int = args.handler(args)
    except (datasets.DatasetError, ValueError, OSError) as exc:
        sys.stderr.write(f"eval harness error: {exc}\n")
        return EXIT_HARNESS_ERROR
    return code
