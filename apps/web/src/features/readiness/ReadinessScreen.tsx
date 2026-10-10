"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useEffect } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { KpiCard } from "@/components/ui/StatCard";
import { DataTable, type Column } from "@/components/ui/Table";
import { TabNav } from "@/components/ui/TabNav";
import { Value } from "@/components/ui/Value";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { useSectionOptions } from "@/features/findings/data";
import { isRunFinished, RunProgress } from "@/features/findings/RunChecks";
import type { DqRun } from "@/features/findings/types";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatCount, formatDateTime } from "@/lib/format";
import {
  READINESS_KEYS,
  useReadinessProfiles,
  useReadinessSummary,
  useSectionReadiness,
} from "./data";
import { OwnerBadge, ReadinessBadge, ReadyBar } from "./parts";
import { slipHref, type ReadinessSection, type ReadinessStudent } from "./types";

const runSchema = z.object({});

/** Reloads the readiness screen once a check it started has finished. */
function RefreshWhenDone({ run, profile }: { run: DqRun; profile: string }) {
  const queryClient = useQueryClient();
  const finished = isRunFinished(run.status);
  useEffect(() => {
    if (finished)
      void queryClient.invalidateQueries({ queryKey: [...READINESS_KEYS.all, profile] });
  }, [finished, profile, queryClient]);
  return null;
}

/** "Check now" for one readiness profile: the section shown, or the profile's classes. */
function RunReadinessDialog({ profile, sectionId }: { profile: string; sectionId: string | null }) {
  const t = useTranslations("readiness.run");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("open")}
      triggerVariant="primary"
      title={t("title")}
      description={sectionId ? t("descriptionSection") : t("descriptionProfile")}
      confirmLabel={t("start")}
      schema={runSchema}
      errorNamespace="findings"
      submit={(_data, key) =>
        unwrap(
          api.POST("/api/v1/dq/readiness/{profile_key}/runs", {
            params: { path: { profile_key: profile } },
            headers: { "Idempotency-Key": key },
            body: { scope: sectionId ? { section_ids: [sectionId] } : {} },
          }),
        )
      }
      renderResult={(run, close) => (
        <>
          <RunProgress runId={run.id} initial={run} />
          <RefreshWhenDone run={run} profile={profile} />
          <div className="flex justify-end">
            <Button variant="secondary" onClick={close}>
              {tc("close")}
            </Button>
          </div>
        </>
      )}
    />
  );
}

/**
 * Board readiness (US-503, US-505; FR-DQ-032..FR-DQ-036): pick a board or portal (AP SSC 2027,
 * APAAR), see how many students of each section are ready, who must act for the rest, open a
 * section's students and print parent verification slips. Class teachers see their sections.
 */
export function ReadinessScreen({
  profileKey,
  sectionId,
}: {
  profileKey: string;
  sectionId: string | null;
}) {
  const t = useTranslations("readiness");
  const tf = useTranslations("findings");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const profiles = useReadinessProfiles();
  const summary = useReadinessSummary(profileKey);
  const { sections } = useSectionOptions();
  const students = useSectionReadiness(profileKey, sectionId);
  const count = (value: number | null | undefined) => formatCount(value, locale) ?? "0";
  const sectionLabel = (id: string) => sections.find((s) => s.id === id)?.label ?? t("section");
  const profileName = (key: string) => {
    const found = profiles.data?.find((p) => p.key === key);
    if (!found) return key;
    return locale === "te" && found.label_te ? found.label_te : found.label_en;
  };
  const data = summary.data;
  const totals = data?.totals;

  const sectionColumns: Column<ReadinessSection>[] = [
    {
      key: "section",
      stack: "title",
      header: t("colSection"),
      cell: (row) => <span className="font-semibold">{sectionLabel(row.section_id)}</span>,
    },
    {
      key: "ready",
      header: t("colReady"),
      cell: (row) => (
        <ReadyBar
          ready={row.ready}
          students={row.students}
          label={t("readyOf", { ready: count(row.ready), students: count(row.students) })}
        />
      ),
    },
    {
      key: "parent",
      numeric: true,
      header: t("status.needs_parent"),
      cell: (row) => count(row.needs_parent),
    },
    {
      key: "school",
      numeric: true,
      header: t("status.needs_school"),
      cell: (row) => count(row.needs_school),
    },
    {
      key: "blocked",
      numeric: true,
      header: t("status.blocked"),
      cell: (row) => count(row.blocked),
    },
    {
      key: "open",
      stack: "actions",
      header: tc("actions"),
      className: "print:hidden",
      cell: (row) => (
        <Link
          href={`/findings/readiness?profile=${profileKey}&section=${row.section_id}`}
          className="font-semibold whitespace-nowrap text-primary underline-offset-4 hover:underline"
          aria-current={row.section_id === sectionId ? "true" : undefined}
        >
          {t("openSection")}
          <span className="sr-only">: {sectionLabel(row.section_id)}</span>
        </Link>
      ),
    },
  ];

  const studentColumns: Column<ReadinessStudent>[] = [
    {
      key: "student",
      stack: "title",
      header: tf("colStudent"),
      cell: (row) => (
        <span className="block min-w-36">
          <span className="block font-semibold">
            <Value>{row.student.display_name}</Value>
          </span>
          {row.student.admission_no ? (
            <span className="block font-mono text-xs text-ink-muted">
              {tf("admissionNo", { number: row.student.admission_no })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <ReadinessBadge status={row.status} />,
    },
    {
      key: "owners",
      header: t("colWhoActs"),
      className: "min-w-48",
      cell: (row) =>
        row.owners.length > 0 ? (
          <span className="flex flex-wrap gap-1">
            {row.owners.map((owner) => (
              <OwnerBadge key={owner} owner={owner} />
            ))}
          </span>
        ) : (
          <span className="text-ink-muted">{t("nothingToDo")}</span>
        ),
    },
    {
      key: "fields",
      header: t("colFields"),
      cell: (row) => count(row.open_items),
    },
    {
      key: "actions",
      stack: "actions",
      header: tc("actions"),
      className: "print:hidden",
      cell: (row) => (
        <span className="flex flex-wrap gap-x-4 gap-y-1">
          <Link
            href={`/findings/readiness/${row.student.id}?profile=${profileKey}`}
            className="font-semibold whitespace-nowrap text-primary underline-offset-4 hover:underline"
          >
            {t("openStudent")}
            <span className="sr-only">: {row.student.display_name ?? ""}</span>
          </Link>
          <a
            href={slipHref(profileKey, { studentId: row.student.id })}
            target="_blank"
            rel="noopener noreferrer"
            className="whitespace-nowrap text-primary underline-offset-4 hover:underline"
          >
            {t("printSlip")}
            <span className="sr-only">: {row.student.display_name ?? ""}</span>
          </a>
        </span>
      ),
    },
  ];

  const tabs = (profiles.data ?? []).map((profile) => ({
    id: profile.key,
    href: `/findings/readiness?profile=${profile.key}`,
    label: locale === "te" && profile.label_te ? profile.label_te : profile.label_en,
  }));
  const lastRun = formatDateTime(data?.last_run?.finished_at ?? null);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: tf("crumbHome"), href: "/" },
          { label: tf("title"), href: "/findings" },
          { label: t("title") },
        ]}
        actions={
          <>
            {can("dq.readiness.manage") ? (
              <RunReadinessDialog profile={profileKey} sectionId={sectionId} />
            ) : null}
            <Button variant="secondary" onClick={() => window.print()}>
              {tf("print")}
            </Button>
          </>
        }
      />

      {tabs.length > 0 ? (
        <TabNav label={t("profilePicker")} items={tabs} activeId={profileKey} />
      ) : null}

      {data && !data.profile.verified ? (
        <Alert tone="warning" title={t("unverifiedTitle")}>
          <p>{t("unverifiedBody")}</p>
          {data.profile.source.length > 0 ? (
            <details className="mt-2 print:hidden">
              <summary className="cursor-pointer font-semibold">{t("sources")}</summary>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-sm break-all">
                {data.profile.source.map((url) => (
                  <li key={url}>
                    <a href={url} target="_blank" rel="noopener noreferrer" className="underline">
                      {url}
                    </a>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </Alert>
      ) : null}

      {summary.isError ? (
        <ApiErrorAlert error={summary.error} />
      ) : summary.isPending ? (
        <LoadingState label={tc("loading")} rows={3} />
      ) : (
        <section aria-labelledby="readiness-summary" className="space-y-3">
          <h2 id="readiness-summary" className="sr-only">
            {t("summaryTitle", { profile: profileName(profileKey) })}
          </h2>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <KpiCard
              label={t("kpiReady")}
              value={t("readyOf", {
                ready: count(totals?.ready),
                students: count(totals?.students),
              })}
              unavailableLabel={tc("notAvailable")}
              aside={<ReadinessBadge status="ready" />}
            />
            <KpiCard
              label={t("status.needs_parent")}
              value={count(totals?.needs_parent)}
              unavailableLabel={tc("notAvailable")}
              aside={<OwnerBadge owner="parent_aadhaar" />}
            />
            <KpiCard
              label={t("status.needs_school")}
              value={count(totals?.needs_school)}
              unavailableLabel={tc("notAvailable")}
              aside={<OwnerBadge owner="school_udise" />}
            />
            <KpiCard
              label={t("status.blocked")}
              value={count(totals?.blocked)}
              unavailableLabel={tc("notAvailable")}
              aside={<OwnerBadge owner="unknown" />}
            />
          </div>
          <p className="inline-flex flex-wrap items-center gap-2 text-sm text-ink-muted">
            <span>{t("lastRun")}:</span>
            <Pill variant="date">{lastRun ?? t("neverRun")}</Pill>
            <span>{t("liveNote")}</span>
          </p>
          {data && data.sections.length === 0 ? (
            <EmptyState
              icon="checkCircle"
              title={t("noSectionsTitle")}
              body={t("noSectionsBody")}
            />
          ) : (
            <Card title={t("sectionsTitle")} description={t("sectionsHint")}>
              <DataTable
                stacked
                caption={t("sectionsTitle")}
                captionHidden
                columns={sectionColumns}
                state={{ status: "ready", data: data?.sections ?? [] }}
                rowKey={(row) => row.section_id}
                emptyTitle={t("noSectionsTitle")}
              />
            </Card>
          )}
        </section>
      )}

      {sectionId ? (
        <Card
          title={t("studentsTitle", { section: sectionLabel(sectionId) })}
          description={t("studentsHint")}
          actions={
            <a
              href={slipHref(profileKey, { sectionId })}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-semibold text-primary underline print:hidden"
            >
              {t("printSectionSlips")}
            </a>
          }
        >
          {students.isError ? (
            <ApiErrorAlert error={students.error} />
          ) : (
            <DataTable
              stacked
              caption={t("studentsTitle", { section: sectionLabel(sectionId) })}
              captionHidden
              columns={studentColumns}
              state={
                students.isPending
                  ? { status: "loading" }
                  : { status: "ready", data: students.data ?? [] }
              }
              rowKey={(row) => row.student.id}
              emptyTitle={t("noStudents")}
            />
          )}
        </Card>
      ) : null}
    </div>
  );
}
