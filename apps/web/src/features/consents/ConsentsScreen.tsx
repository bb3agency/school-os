"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { ButtonLink, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { PdfJobButton } from "@/features/exports/PdfJobButton";
import { ReadyBar } from "@/features/readiness/parts";
import { useSchoolStructure, useSectionOptions } from "@/features/students/StudentList";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount } from "@/lib/format";
import {
  ANSWERS,
  CONSENT_KEYS,
  CONSENT_READ,
  CONSENT_RECORD,
  SETTINGS_MANAGE,
  formsHref,
  type AnswerCounts,
  type PurposeSummary,
} from "./types";

type SectionRow = PurposeSummary["sections"][number];

/**
 * Consent register summary (US-2603, FR-DPDP-026, ADR-0049): for each optional purpose, how
 * many families agreed, did not agree, withdrew, have the form at home or have not answered,
 * for the whole school and per section (counts only; class teachers see their sections, the
 * API decides). The forms for a section print from here, or as a PDF made in the background.
 * Not agreeing is a full answer: nothing here asks staff to chase a "yes".
 */
export function ConsentsScreen() {
  const t = useTranslations("consents");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can([CONSENT_READ, CONSENT_RECORD]);
  const structure = useSchoolStructure();
  const sections = useSectionOptions(structure);
  const [sectionId, setSectionId] = useState("");
  const [purposeId, setPurposeId] = useState("");

  const summary = useQuery({
    queryKey: CONSENT_KEYS.summary(sectionId, purposeId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/consents/summary", {
          params: {
            query: {
              ...(sectionId ? { section_id: sectionId } : {}),
              ...(purposeId ? { purpose_id: purposeId } : {}),
            },
          },
        }),
      ),
    enabled: allowed,
    retry: false,
  });
  // The purpose filter lists every active purpose, also while one is chosen.
  const purposes = useQuery({
    queryKey: CONSENT_KEYS.purposes,
    queryFn: () => unwrap(api.GET("/api/v1/consent-purposes")),
    enabled: allowed,
    retry: false,
  });
  const purposeOptions = (purposes.data?.data ?? [])
    .filter((purpose) => purpose.active)
    .map((purpose) => ({ value: purpose.id, label: purpose.label_en }));
  const sectionLabel = sections.find((option) => option.value === sectionId)?.label;

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        allowed && sectionId ? (
          <a
            href={formsHref({ sectionId })}
            target="_blank"
            rel="noopener"
            className={buttonClasses("primary")}
          >
            {t("printForms")}
            <span className="sr-only">: {String(sectionLabel ?? "")}</span>
          </a>
        ) : undefined
      }
    />
  );

  if (!meLoaded) {
    return (
      <div className="space-y-5">
        {header}
        <LoadingState label={tc("loading")} />
      </div>
    );
  }
  if (!allowed) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  const list = summary.data?.purposes ?? [];
  return (
    <div className="space-y-5">
      {header}
      <Alert tone="info" title={t("refusalTitle")}>
        {t("refusalBody")}
      </Alert>

      <section
        aria-label={t("filtersTitle")}
        className="grid gap-3 sm:flex sm:flex-wrap sm:items-end sm:gap-4"
        data-print="hide"
      >
        <SelectField
          label={t("section")}
          placeholder={t("allSections")}
          value={sectionId}
          className="sm:w-72"
          onChange={(event) => setSectionId(event.currentTarget.value)}
          options={sections}
        />
        <SelectField
          label={t("purpose")}
          placeholder={t("allPurposes")}
          value={purposeId}
          className="sm:w-80"
          onChange={(event) => setPurposeId(event.currentTarget.value)}
          options={purposeOptions}
        />
      </section>

      <FormsCard sectionId={sectionId} sectionLabel={sectionLabel ? String(sectionLabel) : ""} />

      <section aria-labelledby="consents-summary" className="space-y-4">
        <h2 id="consents-summary" className="text-lg font-semibold text-ink">
          {t("summaryTitle")}
        </h2>
        {summary.isPending ? (
          <LoadingState label={tc("loading")} />
        ) : summary.error ? (
          <ApiErrorAlert error={summary.error} namespace="consents" />
        ) : list.length === 0 ? (
          <EmptyState
            title={t("noPurposesTitle")}
            body={can(SETTINGS_MANAGE) ? t("noPurposesBody") : t("noPurposesBodyReadOnly")}
            action={
              can(SETTINGS_MANAGE) ? (
                <ButtonLink href="/settings/consent-purposes" variant="secondary">
                  {t("setUpPurposes")}
                </ButtonLink>
              ) : undefined
            }
          />
        ) : (
          list.map((purpose) => <PurposeCard key={purpose.purpose_id} purpose={purpose} />)
        )}
      </section>
    </div>
  );
}

/** The forms of one section: print (all, or only students with an unanswered purpose) or PDF. */
function FormsCard({ sectionId, sectionLabel }: { sectionId: string; sectionLabel: string }) {
  const t = useTranslations("consents");
  return (
    <Card title={t("formsTitle")} description={t("formsDescription")}>
      {sectionId ? (
        <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start">
          <a
            href={formsHref({ sectionId }, { onlyUnanswered: true })}
            target="_blank"
            rel="noopener"
            className={buttonClasses("secondary", "sm")}
          >
            {t("printUnanswered")}
            <span className="sr-only">: {sectionLabel}</span>
          </a>
          <PdfJobButton
            label={`${t("pdfUnanswered")}: ${sectionLabel}`}
            request={{
              kind: "consent_forms",
              body: { section_id: sectionId, only_unanswered: true },
            }}
          />
          <PdfJobButton
            label={`${t("pdfAll")}: ${sectionLabel}`}
            request={{
              kind: "consent_forms",
              body: { section_id: sectionId, only_unanswered: false },
            }}
          />
        </div>
      ) : (
        <p className="text-sm text-ink-muted">{t("chooseSectionToPrint")}</p>
      )}
    </Card>
  );
}

function Counts({ counts }: { counts: AnswerCounts }) {
  const t = useTranslations("consents");
  const locale = useLocale() as Locale;
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3 lg:grid-cols-6">
      <div>
        <dt className="text-ink-muted">{t("colTotal")}</dt>
        <dd className="num text-base font-semibold text-ink">
          {formatCount(counts.total, locale)}
        </dd>
      </div>
      {ANSWERS.map((answer) => (
        <div key={answer}>
          <dt className="text-ink-muted">{t(`answers.${answer}`)}</dt>
          <dd className="num text-base font-semibold text-ink">
            {formatCount(counts[answer], locale)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function PurposeCard({ purpose }: { purpose: PurposeSummary }) {
  const t = useTranslations("consents");
  const locale = useLocale() as Locale;
  const agreed = t("agreedOf", {
    given: formatCount(purpose.totals.given, locale) ?? "0",
    total: formatCount(purpose.totals.total, locale) ?? "0",
  });
  const columns: ColumnV2<SectionRow>[] = [
    {
      key: "section",
      stack: "title",
      header: t("colClass"),
      cell: (row) => <span className="font-semibold">{row.class_section}</span>,
    },
    {
      key: "agreed",
      header: t("colAgreed"),
      cell: (row) => (
        <ReadyBar
          ready={row.counts.given}
          students={row.counts.total}
          label={t("agreedOf", {
            given: formatCount(row.counts.given, locale) ?? "0",
            total: formatCount(row.counts.total, locale) ?? "0",
          })}
        />
      ),
    },
    ...ANSWERS.filter((answer) => answer !== "given").map((answer) => ({
      key: answer,
      header: t(`answers.${answer}`),
      numeric: true,
      cell: (row: SectionRow) => formatCount(row.counts[answer], locale),
    })),
  ];
  return (
    <Card title={purpose.label_en}>
      <div className="space-y-4">
        <ReadyBar ready={purpose.totals.given} students={purpose.totals.total} label={agreed} />
        <Counts counts={purpose.totals} />
        {purpose.sections.length > 0 ? (
          <details className="group">
            <summary className="inline-flex min-h-6 cursor-pointer items-center text-sm font-semibold text-primary underline-offset-4 hover:underline">
              {t("bySection", { count: purpose.sections.length })}
            </summary>
            <div className="mt-3">
              <DataTableV2
                id="consents-sections"
                stacked
                caption={t("bySectionCaption", { purpose: purpose.label_en })}
                captionHidden
                columns={columns}
                state={{ status: "ready", data: [...purpose.sections] }}
                rowKey={(row) => row.section_id}
                emptyTitle={t("noSectionsTitle")}
                emptyBody={t("noSectionsBody")}
              />
            </div>
          </details>
        ) : null}
      </div>
    </Card>
  );
}
