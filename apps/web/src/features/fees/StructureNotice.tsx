"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Field, TextField } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import type { Locale } from "@/i18n/routing";
import { formatInr } from "@/lib/format";
import { noticePrintHref, useStructureChanges, type ClassChange } from "./family";
import { FeesGate } from "./parts";

interface YearOption {
  id: string;
  label: string;
  is_current: boolean;
}

/** One decimal at most, 0..1000 (the API's `warn_percent`). */
const PERCENT = /^\d{1,4}(\.\d)?$/;

/**
 * Fee setup's "Fee structure notice" card (FM-10; FR-FEE-113..124; D86): print the year's fee
 * structure by class (all classes or one) and see the change against last year, head by head,
 * with increases above the school's review threshold flagged. The threshold is the school's own
 * value (config, not a legal cap); typing another one changes this view only and saves nothing.
 * Plans only: no student data on this card.
 */
export function StructureNoticeCard({
  years,
  classes,
}: {
  years: readonly YearOption[];
  classes: readonly { id: string; display_en: string }[];
}) {
  const t = useTranslations("fees.structureNotice");
  const fallback = years.find((y) => y.is_current) ?? years[0];
  const [yearId, setYearId] = useState<string | null>(fallback?.id ?? null);
  const [classId, setClassId] = useState("");
  const [warn, setWarn] = useState("");
  const yearField = useId();
  const classField = useId();
  const warnOk = warn === "" || PERCENT.test(warn.trim());
  const changes = useStructureChanges(yearId, warnOk ? warn.trim() : "");
  if (yearId === null) return null;
  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <Field label={t("year")} id={yearField} className="w-40">
            {({ id }) => (
              <Select
                id={id}
                options={years.map((y) => ({ value: y.id, label: y.label }))}
                value={yearId}
                onChange={(event) => setYearId(event.target.value)}
              />
            )}
          </Field>
          <Field label={t("class")} id={classField} className="w-48">
            {({ id }) => (
              <Select
                id={id}
                placeholder={t("allClasses")}
                options={classes.map((c) => ({ value: c.id, label: c.display_en }))}
                value={classId}
                onChange={(event) => setClassId(event.target.value)}
              />
            )}
          </Field>
          <a
            className={buttonClasses("primary")}
            href={noticePrintHref(yearId, classId || undefined)}
            target="_blank"
            rel="noopener"
          >
            {t("print")}
          </a>
        </div>
        <p className="text-sm text-ink-muted">{t("printNote")}</p>
        <section
          aria-labelledby={`${yearField}-changes`}
          className="space-y-3 border-t border-border pt-4"
        >
          <h3 id={`${yearField}-changes`} className="text-base font-semibold text-ink">
            {t("changesTitle")}
          </h3>
          <TextField
            label={t("threshold")}
            hint={t("thresholdHint")}
            error={warnOk ? undefined : t("thresholdError")}
            inputMode="decimal"
            className="w-56 [&_input]:num"
            value={warn}
            onChange={(event) => setWarn(event.target.value)}
          />
          <FeesGate data={changes}>
            {(data) =>
              data.classes.length === 0 ? (
                <EmptyState icon="layers" title={t("noPlans")} body={t("noPlansBody")} />
              ) : (
                <>
                  <p className="text-sm">
                    {data.previous_year_label
                      ? t("against", {
                          year: data.academic_year_label,
                          previous: data.previous_year_label,
                          percent: data.warn_percent,
                        })
                      : t("firstYear", { year: data.academic_year_label })}
                  </p>
                  {data.classes_above_threshold > 0 ? (
                    <Alert
                      tone="warning"
                      title={t("flaggedTitle", { count: data.classes_above_threshold })}
                    >
                      {t("flaggedBody", { percent: data.warn_percent })}
                    </Alert>
                  ) : null}
                  {data.classes.map((row) => (
                    <ClassChanges key={row.class_id} row={row} />
                  ))}
                </>
              )
            }
          </FeesGate>
        </section>
      </div>
    </Card>
  );
}

function percentText(value: string | null): string {
  if (value === null) return "—";
  return value.startsWith("-") ? `${value}%` : `+${value}%`;
}

function ClassChanges({ row }: { row: ClassChange }) {
  const t = useTranslations("fees.structureNotice");
  const locale = useLocale() as Locale;
  const money = (value: string | null) => (value === null ? "—" : formatInr(value, locale));
  return (
    <div className="space-y-1">
      <h4 className="text-sm font-semibold text-ink">
        {row.class_label}
        {row.plan_status === "draft" ? (
          <span className="ms-2 text-xs font-normal text-ink-muted">{t("draft")}</span>
        ) : null}
        {row.plan_status === null ? (
          <span className="ms-2 text-xs font-normal text-ink-muted">{t("noPlanThisYear")}</span>
        ) : null}
      </h4>
      <TableScroll label={t("tableLabel", { name: row.class_label })}>
        <Table density="compact">
          <THead>
            <Tr>
              <Th>{t("columns.head")}</Th>
              <Th>{t("columns.change")}</Th>
              <Th numeric>{t("columns.previous")}</Th>
              <Th numeric>{t("columns.current")}</Th>
              <Th numeric>{t("columns.difference")}</Th>
              <Th numeric>{t("columns.percent")}</Th>
            </Tr>
          </THead>
          <TBody>
            {row.heads.map((h) => (
              <Tr key={h.head_id}>
                <Td>{h.name}</Td>
                <Td>{t(`kinds.${h.change}`)}</Td>
                <Td numeric>{money(h.previous_amount)}</Td>
                <Td numeric>{money(h.current_amount)}</Td>
                <Td numeric>{money(h.difference)}</Td>
                <Td
                  numeric
                  className={h.above_threshold ? "font-semibold text-warning-ink" : undefined}
                >
                  {percentText(h.percent)}
                  {h.above_threshold ? (
                    <span className="sr-only"> {t("aboveThreshold")}</span>
                  ) : null}
                </Td>
              </Tr>
            ))}
            <Tr>
              <Td className="font-semibold">{t("total")}</Td>
              <Td />
              <Td numeric className="font-semibold">
                {row.has_previous ? money(row.previous_total) : "—"}
              </Td>
              <Td numeric className="font-semibold">
                {money(row.current_total)}
              </Td>
              <Td numeric className="font-semibold">
                {money(row.difference)}
              </Td>
              <Td
                numeric
                className={row.above_threshold ? "font-semibold text-warning-ink" : "font-semibold"}
              >
                {percentText(row.percent)}
                {row.above_threshold ? (
                  <span className="sr-only"> {t("aboveThreshold")}</span>
                ) : null}
              </Td>
            </Tr>
          </TBody>
        </Table>
      </TableScroll>
    </div>
  );
}
