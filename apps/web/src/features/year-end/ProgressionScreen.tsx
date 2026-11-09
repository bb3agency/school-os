"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { useStructureLists } from "@/features/academic-structure/data";
import { LoadGate } from "@/features/insights/parts";
import { downloadSheet } from "@/features/sheets/download";
import { useStaffCan } from "@/lib/bff/staff-me";
import {
  YEAR_END_PERM,
  progressionCsvPath,
  useProgression,
  type Progression,
  type ProgressionBlank,
} from "./data";

const BLANKS: readonly ProgressionBlank[] = ["result", "percent", "days_attended", "udise_pen"];

/**
 * FT-08 UDISE+ progression check sheet (FR-YRE-010..FR-YRE-013): per student finishing the
 * year, class, confirmed result, % marks and days attended, with the blanks to fill before the
 * portal. The CSV download is a portal export (step-up MFA; audited by the API).
 */
export function ProgressionScreen() {
  const t = useTranslations("yearEnd.progression");
  const can = useStaffCan();
  const allowed = can(YEAR_END_PERM.read);
  const { years } = useStructureLists();
  const [chosen, setChosen] = useState("");
  const list = years.status === "ready" ? years.data : [];
  const yearId = chosen || (list.find((y) => y.is_current)?.id ?? list[0]?.id ?? "");
  const sheet = useProgression(yearId, allowed);
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <ButtonLink href="/year-end" variant="secondary">
            {t("back")}
          </ButtonLink>
        }
      />
      {allowed ? (
        <>
          <div className="max-w-sm">
            <SelectField
              label={t("year")}
              value={yearId}
              disabled={years.status !== "ready"}
              onChange={(event) => setChosen(event.target.value)}
              options={list.map((y) => ({ value: y.id, label: y.label }))}
            />
          </div>
          {yearId === "" ? null : (
            <LoadGate data={sheet}>
              {(data) => <Sheet data={data} canDownload={can(YEAR_END_PERM.portal)} />}
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

function Sheet({ data, canDownload }: { data: Progression; canDownload: boolean }) {
  const t = useTranslations("yearEnd");
  const tp = useTranslations("yearEnd.progression");
  const locale = useLocale();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(undefined);
    setSaved(null);
    try {
      setSaved(
        await downloadSheet({
          path: progressionCsvPath(data.academic_year_id),
          method: "GET",
          locale,
          fallbackName: "udise-progression.csv",
        }),
      );
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  if (data.rows.length === 0) {
    return <EmptyState icon="users" title={tp("emptyTitle")} body={tp("emptyBody")} />;
  }
  return (
    <Card
      title={tp("cardTitle", { year: data.academic_year_label, count: data.students })}
      description={tp("cardBody")}
      actions={
        canDownload ? (
          <Button variant="secondary" onClick={() => void download()} disabled={busy}>
            <Icon name="arrowDown" className="size-4" />
            {busy ? tp("downloading") : tp("download")}
          </Button>
        ) : null
      }
    >
      <div className="space-y-4">
        <ul className="flex flex-wrap gap-2" aria-label={tp("blanksLabel")}>
          {BLANKS.map((key) => (
            <li key={key}>
              <Pill variant={(data.blank_counts[key] ?? 0) > 0 ? "negative" : "done"}>
                {tp(`blanks.${key}`, { count: data.blank_counts[key] ?? 0 })}
              </Pill>
            </li>
          ))}
        </ul>
        <span role="status" aria-live="polite" className="text-xs text-ink-muted">
          {saved ? tp("downloaded", { name: saved }) : ""}
        </span>
        <ApiErrorAlert error={error} />
        <TableScroll label={tp("tableLabel", { year: data.academic_year_label })}>
          <Table>
            <THead>
              <Tr>
                <Th>{tp("columns.admission")}</Th>
                <Th>{tp("columns.student")}</Th>
                <Th>{tp("columns.pen")}</Th>
                <Th>{tp("columns.section")}</Th>
                <Th>{tp("columns.result")}</Th>
                <Th numeric>{tp("columns.percent")}</Th>
                <Th numeric>{tp("columns.days")}</Th>
                <Th>{tp("columns.blanks")}</Th>
              </Tr>
            </THead>
            <TBody>
              {data.rows.map((row) => (
                <Tr key={row.student_id}>
                  <Td>
                    <span className="font-mono">{row.admission_no ?? "—"}</span>
                  </Td>
                  <Td>{row.full_name ?? t("unnamed")}</Td>
                  <Td>
                    <span className="font-mono">{row.udise_pen ?? "—"}</span>
                  </Td>
                  <Td>{row.section_label}</Td>
                  <Td>{row.result ? t(`result.${row.result}`) : "—"}</Td>
                  <Td numeric>{row.percent === null ? "—" : `${row.percent.toFixed(1)}%`}</Td>
                  <Td numeric>
                    {row.days_attended === null
                      ? "—"
                      : t("daysOf", { days: row.days_attended, working: row.working_days ?? 0 })}
                  </Td>
                  <Td>
                    {row.blanks.length === 0
                      ? tp("complete")
                      : row.blanks.map((b) => tp(`blankNames.${b}`)).join(", ")}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableScroll>
      </div>
    </Card>
  );
}
