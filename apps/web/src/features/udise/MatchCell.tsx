"use client";

import type { components } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { StudentCombobox, type PickedStudent } from "@/features/privacy/SubjectPicker";
import { useBffClient } from "@/lib/bff/query";
import * as z from "@/lib/zod";
import { needsReview, udiseApi, type MatchDecision } from "./api";

/** How a UDISE+ row found its student (FR-UDI-012); `match_basis` of ImportRowOut. */
export const MATCH_BASES: readonly MatchBasis[] = [
  "admission_no",
  "picked",
  "pen_verified",
  "pen",
  "name_dob_class",
  "skipped",
];
export type MatchBasis = NonNullable<components["schemas"]["ImportRowOut"]["match_basis"]>;

const BASIS_VARIANTS: Record<MatchBasis, PillVariant> = {
  admission_no: "done",
  picked: "progress",
  pen_verified: "done",
  pen: "positive",
  name_dob_class: "review",
  skipped: "tag",
};

/** The row fields this cell reads. */
export type MatchRow = Pick<
  components["schemas"]["ImportRowOut"],
  "row_no" | "status" | "errors" | "values" | "match_basis"
>;

function basisOf(row: MatchRow): MatchBasis | null {
  return row.match_basis ?? null;
}

/**
 * FO-04 (FR-UDI-012, FR-UDI-014): the match basis of a UDISE+ row, and for a row the import
 * could not match, the person's decision: pick the student (search by name or admission number)
 * or skip the row. A row matched by a pick or skipped can be undone while the import is open.
 * Nothing is recorded on the student until the import is added, and only as UDISE+ values.
 */
export function MatchCell({
  importId,
  version,
  row,
  editable,
  onChanged,
}: {
  importId: string;
  version: number;
  row: MatchRow;
  editable: boolean;
  onChanged: () => void;
}) {
  const t = useTranslations("udise.match");
  const api = useBffClient("staff");
  const fieldId = useId();
  const [student, setStudent] = useState<PickedStudent | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const basis = basisOf(row);
  const review = needsReview(row.errors);
  const name = row.values.full_name ?? t("rowLabel", { row: row.row_no });

  async function decide(decision: MatchDecision) {
    setPending(true);
    setError(undefined);
    try {
      await udiseApi.matchRow(api, importId, row.row_no, version, decision);
      onChanged();
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <span className="flex flex-col items-start gap-2">
      {basis ? (
        <Pill variant={BASIS_VARIANTS[basis]}>{t(`basis.${basis}`)}</Pill>
      ) : review ? (
        <Pill variant="negative">{t("toReview")}</Pill>
      ) : null}
      {editable && review ? (
        <span className="flex flex-wrap gap-2">
          <ActionDialog
            triggerLabel={t("pick.trigger")}
            triggerSize="sm"
            triggerVariant="primary"
            title={t("pick.title")}
            description={t("pick.description", { name })}
            confirmLabel={t("pick.confirm")}
            confirmDisabled={student === null}
            consequence={t("pick.consequence")}
            schema={z.object({ student_id: z.string().min(1, { error: "required" }) })}
            submit={(data) =>
              udiseApi.matchRow(api, importId, row.row_no, version, "student", data.student_id)
            }
            onSuccess={() => {
              setStudent(null);
              onChanged();
            }}
          >
            {(errors) =>
              student ? (
                <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-muted px-3 py-2">
                  <span className="min-w-0 font-semibold break-words text-ink">
                    {student.label}
                  </span>
                  <input type="hidden" name="student_id" value={student.id} />
                  <Button variant="ghost" size="sm" onClick={() => setStudent(null)}>
                    {t("pick.change")}
                  </Button>
                </div>
              ) : (
                <StudentCombobox fieldId={fieldId} error={errors.student_id} onPick={setStudent} />
              )
            }
          </ActionDialog>
          <Button
            size="sm"
            variant="secondary"
            disabled={pending}
            onClick={() => void decide("skip")}
            aria-label={t("skipAria", { name })}
          >
            {t("skip")}
          </Button>
        </span>
      ) : null}
      {editable && (basis === "picked" || basis === "skipped") ? (
        <Button
          size="sm"
          variant="ghost"
          disabled={pending}
          onClick={() => void decide("clear")}
          aria-label={t("undoAria", { name })}
        >
          {t("undo")}
        </Button>
      ) : null}
      <ApiErrorAlert error={error} namespace="udise" />
    </span>
  );
}
