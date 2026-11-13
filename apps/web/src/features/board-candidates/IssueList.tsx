"use client";

import { useTranslations } from "next-intl";
import type { BoardScheme, CandidateIssue } from "./data";

/** The translator seen with plain string keys, for keys built from API codes (checked first). */
export interface LooseT {
  (key: string, values?: Record<string, string>): string;
  has(key: string): boolean;
}

export function looseT(t: unknown): LooseT {
  return t as LooseT;
}

/** The label of `prefix.code` when the catalogue has one, else the code itself. */
export function labelOr(t: LooseT, prefix: string, code: string): string {
  const key = `${prefix}.${code}`;
  return t.has(key) ? t(key) : code;
}

/** What a candidate still needs, one short line per issue (the same rules as DQ-032). */
export function IssueList({
  issues,
  scheme,
}: {
  issues: readonly CandidateIssue[];
  scheme: BoardScheme;
}) {
  const t = useTranslations("boardCandidates");
  const field = (key: string) => labelOr(looseT(t), "field", key);
  const group = (key: string | null | undefined) =>
    scheme.subjects.find((g) => g.key === key)?.label_en ?? t("field.board_subjects");
  return (
    <ul className="space-y-0.5 text-sm text-warning-ink">
      {issues.map((issue, index) => (
        <li key={`${issue.field}:${issue.reason}:${index}`}>
          {t(`issue.${issue.reason}`, {
            field: field(issue.field),
            group: group(issue.group),
            code: issue.code ?? "",
          })}
        </li>
      ))}
    </ul>
  );
}
