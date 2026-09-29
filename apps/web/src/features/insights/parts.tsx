"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { LoadingState } from "@/components/ui/LoadingState";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { useSectionOptions } from "@/features/findings/data";
import { ApiError } from "@/lib/bff/query";
import { formatDate } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import type { Loadable } from "@/lib/loadable";
import { columnLetter, evidenceValues, type Flag, type FlagStatus, type SheetIssue } from "./data";

/** Loading, not available, error or the content of one query. */
export function LoadGate<T>({
  data,
  children,
}: {
  data: Loadable<T>;
  children: (value: T) => ReactNode;
}) {
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  if (data.status === "loading") return <LoadingState label={tc("loading")} />;
  if (data.status === "unavailable") {
    return (
      <Alert tone="info" title={tc("notAvailableYetTitle")}>
        {tc("notAvailableYetBody")}
      </Alert>
    );
  }
  if (data.status === "error") {
    return (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {data.reason ? te(`load.${data.reason}`) : tc("loadErrorBody")}
      </Alert>
    );
  }
  return <>{children(data.data)}</>;
}

const statusPill: Record<FlagStatus, PillVariant> = {
  open: "review",
  in_progress: "progress",
  closed: "done",
};

export function FlagStatusPill({ status }: { status: FlagStatus }) {
  const t = useTranslations("insights.status");
  return <Pill variant={statusPill[status]}>{t(status)}</Pill>;
}

/** "Overdue" in words (colour is never the only signal). */
export function OverduePill({ overdue }: { overdue: boolean }) {
  const t = useTranslations("insights");
  return overdue ? <Pill variant="negative">{t("overdue")}</Pill> : null;
}

/** Why the rule raised the flag, from the numbers it saw (FR-EW-001: explainable). */
export function FlagReason({ flag }: { flag: Pick<Flag, "rule" | "evidence"> }) {
  const t = useTranslations("insights.why");
  const values = evidenceValues(flag);
  for (const key of ["from", "to"]) {
    const raw = values[key];
    if (typeof raw === "string") values[key] = formatDate(raw) ?? raw;
  }
  return <>{t(flag.rule, values)}</>;
}

const PROBLEM_CODES = new Set([
  "not_in_section",
  "future_date",
  "date_out_of_range",
  "duplicate_entry",
  "too_many_entries",
  "aadhaar_full_number_rejected",
  "marks_over_max",
  "absent_has_no_marks",
  "exam_other_year",
  "exam_name_taken",
  "too_old",
  "not_enrolled",
  "before_flag",
  "owner_not_eligible",
  "threshold_out_of_bounds",
  "rule_required",
  "not_an_import_file",
  "student_column_missing",
  "header_missing",
  "too_many_dates",
  "too_many_subjects",
  "too_many_rows",
  "too_many_columns",
  "max_row_missing",
  "file_unreadable",
  "file_too_complex",
  "not_utf8",
  "no_worksheet",
]);

/** What to fix, per problem the API listed (422), in plain words. */
export function ProblemList({ error }: { error: unknown }) {
  const t = useTranslations("insights.problems");
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const codes = [
    ...new Set((error.problem.errors ?? []).map((e) => e.code).filter((c) => PROBLEM_CODES.has(c))),
  ];
  if (codes.length === 0) return null;
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm text-ink">
      {codes.map((code) => (
        <li key={code}>{translateOr(t, code, "other")}</li>
      ))}
    </ul>
  );
}

/** Sections of the current year the user can see (class teachers: their own). */
export function SectionPicker({
  value,
  onChange,
  label,
}: {
  value: string;
  onChange: (id: string) => void;
  label: string;
}) {
  const t = useTranslations("insights");
  const { sections, loading } = useSectionOptions();
  return (
    <SelectField
      label={label}
      value={value}
      disabled={loading}
      placeholder={t("chooseSection")}
      onChange={(event) => onChange(event.target.value)}
      options={sections.map((s) => ({ value: s.id, label: s.label }))}
    />
  );
}

/** Problems a sheet preview found, with the cell to look at (A1-style). */
export function SheetIssues({ issues, total }: { issues: readonly SheetIssue[]; total: number }) {
  const t = useTranslations("insights.sheet");
  if (issues.length === 0) return null;
  return (
    <div className="space-y-2">
      <Alert tone="warning" title={t("issuesTitle", { count: total })}>
        {t("issuesBody")}
      </Alert>
      <TableScroll label={t("issuesTitle", { count: total })}>
        <Table>
          <THead>
            <Tr>
              <Th>{t("cell")}</Th>
              <Th>{t("problem")}</Th>
            </Tr>
          </THead>
          <TBody>
            {issues.map((issue) => (
              <Tr key={`${issue.row}-${issue.column ?? 0}-${issue.code}`}>
                <Td>
                  <span className="font-mono">
                    {issue.column
                      ? `${columnLetter(issue.column)}${issue.row}`
                      : t("row", { row: issue.row })}
                  </span>
                </Td>
                <Td>{translateOr(t, `codes.${issue.code}`, "codes.other")}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableScroll>
    </div>
  );
}

/** The purpose limit, said where insights are shown (08 §4 PRV-003..005). */
export function PurposeNote() {
  const t = useTranslations("insights");
  return <p className="text-sm text-ink-muted">{t("purpose")}</p>;
}
