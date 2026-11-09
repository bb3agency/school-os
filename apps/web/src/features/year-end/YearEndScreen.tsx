"use client";

import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { LoadGate, SectionPicker } from "@/features/insights/parts";
import { useApiMutation, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import {
  YEAR_END_KEYS,
  YEAR_END_PERM,
  YEAR_RESULTS,
  changedEntries,
  choiceProblem,
  confirmYearResults,
  initialChoices,
  useYearResults,
  type Choice,
  type YearResults,
  type YearResultsWriteOut,
} from "./data";

/**
 * FT-08 year-end results (FR-YRE-002..FR-YRE-007, FR-YRE-019): for one section, SchoolOS's
 * proposal per student (from the year's marks and the register) next to the confirmed
 * result. The principal confirms them (all at once or one by one); "detained" needs a reason.
 * In a no-detention class a detained result is saved with a warning, never refused. Nothing
 * here moves a student: the promotion does, with the held-back list pre-filled.
 */
export function YearEndScreen() {
  const t = useTranslations("yearEnd");
  const can = useStaffCan();
  const [section, setSection] = useState("");
  const allowed = can(YEAR_END_PERM.read);
  const results = useYearResults(section, allowed);
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          allowed ? (
            <ButtonLink href="/year-end/progression" variant="secondary">
              {t("progressionLink")}
            </ButtonLink>
          ) : null
        }
      />
      {allowed ? (
        <>
          <div className="max-w-sm">
            <SectionPicker value={section} onChange={setSection} label={t("section")} />
          </div>
          {section === "" ? (
            <EmptyState icon="users" title={t("pickTitle")} body={t("pickBody")} />
          ) : (
            <LoadGate data={results}>
              {(data) => (
                <ResultsForm
                  key={`${data.section.id}-${data.counts.confirmed}`}
                  data={data}
                  canConfirm={can(YEAR_END_PERM.confirm)}
                />
              )}
            </LoadGate>
          )}
        </>
      ) : (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      )}
    </div>
  );
}

function RulesNote({ data }: { data: YearResults }) {
  const t = useTranslations("yearEnd");
  return (
    <Alert tone="info" title={t("rules.title")}>
      <p>
        {t("rules.passMark", { percent: data.rules.pass_percent })}{" "}
        {data.rules.pass_percent_verified ? null : t("rules.unverified")}
      </p>
      {data.no_detention_class ? (
        <p className="mt-1">
          {t("rules.noDetention")} {data.rules.no_detention_verified ? null : t("rules.unverified")}
        </p>
      ) : null}
      <p className="mt-1">{t("rules.days")}</p>
    </Alert>
  );
}

function ResultsForm({ data, canConfirm }: { data: YearResults; canConfirm: boolean }) {
  const t = useTranslations("yearEnd");
  const api = useBffClient("staff");
  const reasonHint = useId();
  const [choices, setChoices] = useState<Record<string, Choice>>(() =>
    initialChoices(data.students),
  );
  const [saved, setSaved] = useState<YearResultsWriteOut | null>(null);
  const entries = changedEntries(data.students, choices);
  const problems = data.students
    .map((row) => {
      const choice = choices[row.student.student_id];
      return choice ? choiceProblem(choice) : null;
    })
    .filter((p) => p !== null);
  const save = useApiMutation(() => confirmYearResults(api, data.section.id, entries), {
    invalidate: [YEAR_END_KEYS.all],
    onSuccess: setSaved,
  });

  if (data.students.length === 0) {
    return <EmptyState icon="users" title={t("emptyTitle")} body={t("emptyBody")} />;
  }

  const update = (id: string, patch: Partial<Choice>) =>
    setChoices((current) => ({
      ...current,
      [id]: { result: "", reason: "", ...current[id], ...patch },
    }));

  return (
    <Card
      title={t("cardTitle", { section: data.section.label, year: data.academic_year_label })}
      description={t("counts", {
        confirmed: data.counts.confirmed,
        students: data.counts.students,
        detained: data.counts.detained,
      })}
    >
      <div className="space-y-4">
        <RulesNote data={data} />
        <TableScroll label={t("tableLabel", { section: data.section.label })}>
          <Table>
            <THead>
              <Tr>
                <Th>{t("columns.roll")}</Th>
                <Th>{t("columns.student")}</Th>
                <Th numeric>{t("columns.percent")}</Th>
                <Th numeric>{t("columns.days")}</Th>
                <Th>{t("columns.proposed")}</Th>
                <Th>{t("columns.decision")}</Th>
              </Tr>
            </THead>
            <TBody>
              {data.students.map((row) => {
                const id = row.student.student_id;
                const choice = choices[id] ?? { result: "", reason: "" };
                const problem = choiceProblem(choice);
                const name = row.student.full_name ?? t("unnamed");
                return (
                  <Tr key={id}>
                    <Td>
                      <span className="num font-mono">{row.student.roll_no ?? "—"}</span>
                    </Td>
                    <Td>
                      <span className="block">{name}</span>
                      <span className="block text-xs text-ink-muted">
                        {row.student.admission_no ?? ""}
                      </span>
                    </Td>
                    <Td numeric>
                      {row.proposal.percent === null ? "—" : `${row.proposal.percent.toFixed(1)}%`}
                    </Td>
                    <Td numeric>
                      {row.proposal.working_days === null
                        ? "—"
                        : t("daysOf", {
                            days: row.proposal.days_attended ?? 0,
                            working: row.proposal.working_days,
                          })}
                    </Td>
                    <Td>
                      {row.proposal.result ? (
                        <Pill variant="review">{t(`result.${row.proposal.result}`)}</Pill>
                      ) : (
                        <span className="text-sm text-ink-muted">{t("noProposal")}</span>
                      )}
                    </Td>
                    <Td>
                      {canConfirm ? (
                        <div className="flex min-w-48 flex-col gap-2">
                          <Select
                            aria-label={t("decisionFor", { name })}
                            value={choice.result}
                            placeholder={t("choose")}
                            onChange={(event) =>
                              update(id, { result: event.target.value as Choice["result"] })
                            }
                            options={YEAR_RESULTS.map((code) => ({
                              value: code,
                              label: t(`result.${code}`),
                            }))}
                          />
                          {choice.result === "detained" ? (
                            <Input
                              aria-label={t("reasonFor", { name })}
                              aria-describedby={reasonHint}
                              aria-invalid={problem !== null || undefined}
                              value={choice.reason}
                              maxLength={500}
                              placeholder={t("reasonPlaceholder")}
                              onChange={(event) => update(id, { reason: event.target.value })}
                            />
                          ) : null}
                          {problem ? (
                            <span className="text-xs text-danger">{t(`problems.${problem}`)}</span>
                          ) : null}
                        </div>
                      ) : row.confirmed ? (
                        <Pill variant="done">{t(`result.${row.confirmed.result}`)}</Pill>
                      ) : (
                        <span className="text-sm text-ink-muted">{t("notConfirmed")}</span>
                      )}
                      {row.confirmed ? (
                        <span className="mt-1 block text-xs text-ink-muted">
                          {t("confirmedBy", {
                            name: row.confirmed.confirmed_by_name ?? t("someone"),
                            date: formatDate(row.confirmed.confirmed_at) ?? "",
                          })}
                        </span>
                      ) : null}
                      {row.warnings.includes("no_detention_class") ||
                      (data.no_detention_class && choice.result === "detained") ? (
                        <span className="mt-1 block text-xs text-warning-ink">
                          {t("warnings.no_detention_class")}
                        </span>
                      ) : null}
                    </Td>
                  </Tr>
                );
              })}
            </TBody>
          </Table>
        </TableScroll>
        <p id={reasonHint} className="text-sm text-ink-muted">
          {t("reasonHint")}
        </p>
        {canConfirm ? (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              onClick={() => save.mutate(undefined)}
              disabled={entries.length === 0 || problems.length > 0 || save.isPending}
              loading={save.isPending}
            >
              {t("save", { count: entries.length })}
            </Button>
            <span className="text-sm text-ink-muted">{t("saveHint")}</span>
          </div>
        ) : (
          <p className="text-sm text-ink-muted">{t("readOnly")}</p>
        )}
        <ApiErrorAlert error={save.error} />
        {saved ? (
          <Alert
            tone={saved.warnings.length > 0 ? "warning" : "success"}
            live
            title={t("savedTitle")}
          >
            {t("saved", { written: saved.written, unchanged: saved.unchanged })}
            {saved.warnings.length > 0
              ? ` ${t("savedWarnings", { count: saved.warnings.length })}`
              : ""}
          </Alert>
        ) : null}
        <p className="text-sm text-ink-muted">{t("promotionNote")}</p>
      </div>
    </Card>
  );
}
