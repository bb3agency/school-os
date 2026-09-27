"""Per-school manifest of the injected mismatches and the expected findings (docs/12 §3).

Written by ``make seed-synthetic`` to ``<out-dir>/manifest-<code>.json`` for tests and evals.
IDs, codes and counts only: admission numbers, rule IDs, injection kinds, register-page
titles and sections; never names, dates of birth or Aadhaar-like numbers. The content is a pure
function of the plan (:meth:`app.devtools.students.SchoolStudents.manifest`), so a test can
also rebuild it without the file. ``expected_scores`` is the in-memory school-scale DQ run
(:func:`app.devtools.dq_eval.school_score`) that a real run should reproduce.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.devtools import dq_eval
from app.devtools.plan import DatasetPlan

if TYPE_CHECKING:
    from app.devtools.seeder import SeedSummary, StudentOptions


def manifest_for(
    plan: DatasetPlan, index: int, options: StudentOptions, *, tenant_id: str
) -> dict[str, Any]:
    tenant = plan.tenants[index]
    school = options.build(tenant)
    data = school.manifest()
    data |= {
        "dataset_version": plan.dataset_version,
        "seed": plan.seed,
        "tenant_id": tenant_id,
        "expected_scores": dq_eval.summary(dq_eval.school_score(school)),
    }
    if options.documents:
        data["register_pages"] = [
            {
                "batch": p.batch_no,
                "page": p.page_no,
                "title": p.title,
                "section": list(p.section),
                "admission_nos": list(p.admission_nos),
                "has_aadhaar_like_number": p.has_aadhaar_like,
            }
            for p in options.pages(tenant, school)
        ]
    return data


def write_manifests(
    summary: SeedSummary, plan: DatasetPlan, *, out_dir: str | Path, options: StudentOptions
) -> list[Path]:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, tenant in enumerate(summary.tenants):
        if tenant.students is None or not tenant.students.students:
            continue
        data = manifest_for(plan, index, options, tenant_id=str(tenant.tenant_id))
        path = directory / f"manifest-{tenant.code}.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8"
        )
        written.append(path)
    return written
