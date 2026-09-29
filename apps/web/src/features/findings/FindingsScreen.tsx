"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { KpiCard } from "@/components/ui/StatCard";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatCount, formatDateTime } from "@/lib/format";
import {
  attributeLabel,
  DQ_KEYS,
  profileLabel,
  ruleText,
  useAttributes,
  useProfiles,
  useRules,
  useSectionOptions,
} from "./data";
import { findingsQuery, type FindingFilters } from "./filters";
import { FindingStatusBadge, FindingValues, SeverityBadge } from "./parts";
import { RunChecksDialog } from "./RunChecks";
import { FINDING_STATUSES, pick, RULE_IDS, SEVERITIES, type Finding } from "./types";

const PAGE_SIZE = 100;

function Checkbox({
  name,
  value,
  label,
  defaultChecked,
}: {
  name: string;
  value: string;
  label: string;
  defaultChecked: boolean;
}) {
  return (
    <label className="inline-flex min-h-8 items-center gap-2 text-sm">
      <input
        type="checkbox"
        name={name}
        value={value}
        defaultChecked={defaultChecked}
        className="size-4 accent-action"
      />
      {label}
    </label>
  );
}

/**
 * Findings list (US-501, US-502; FR-DQ-006): summary counts, filters in the URL, blockers
 * shown before warnings, the conflicting values (masked where sensitive) with their sources,
 * the explanation in the reader's language and the suggested correction. Prints on A4.
 */
export function FindingsScreen({ filters }: { filters: FindingFilters }) {
  const t = useTranslations("findings");
  const tc = useTranslations("common");
  const tsev = useTranslations("findings.severity");
  const tstatus = useTranslations("findings.status");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const can = useStaffCan();
  const rules = useRules();
  const profiles = useProfiles();
  const attributes = useAttributes();
  const { sections } = useSectionOptions();
  const query = findingsQuery(filters);

  const summary = useQuery({
    queryKey: [...DQ_KEYS.summary, filters.profileKey, filters.sectionId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/dq/summary", {
          params: {
            query: {
              ...(filters.profileKey ? { profile_key: filters.profileKey } : {}),
              ...(filters.sectionId ? { section_ids: [filters.sectionId] } : {}),
            },
          },
        }),
      ),
    retry: false,
  });

  const findings = useInfiniteQuery({
    queryKey: [...DQ_KEYS.findings, "list", query],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/dq/findings", {
          params: {
            query: { ...query, limit: PAGE_SIZE, ...(pageParam ? { cursor: pageParam } : {}) },
          },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    retry: false,
  });

  const rows = findings.data?.pages.flatMap((one) => one.data) ?? [];
  const state = toLoadable({
    isPending: findings.isPending,
    isError: findings.isError,
    error: findings.error,
    data: rows,
  });
  const blockers = rows.filter((row) => row.blocker);
  const others = rows.filter((row) => !row.blocker);
  const count = (value: number | null | undefined) => formatCount(value, locale);

  const columns: Column<Finding>[] = [
    {
      key: "severity",
      header: t("colSeverity"),
      cell: (row) => <SeverityBadge severity={row.severity} />,
    },
    {
      key: "student",
      header: t("colStudent"),
      cell: (row) => (
        <span className="block min-w-36">
          <span className="block font-medium">
            <Value>{row.student.display_name}</Value>
          </span>
          {row.student.admission_no ? (
            <span className="block font-mono text-xs whitespace-nowrap text-ink-muted">
              {t("admissionNo", { number: row.student.admission_no })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "field",
      header: t("colField"),
      className: "min-w-32",
      cell: (row) => (
        <span>
          <Value>{attributeLabel(attributes.data, row.attribute_key, locale)}</Value>
          <span className="block font-mono text-xs text-ink-muted">{row.rule_id}</span>
        </span>
      ),
    },
    { key: "values", header: t("colValues"), cell: (row) => <FindingValues values={row.values} /> },
    {
      key: "explanation",
      header: t("colExplanation"),
      className: "min-w-64",
      cell: (row) => (
        <span className="block space-y-1" lang={locale}>
          <span className="block">{pick(row.explanation, locale)}</span>
          {row.match_explanation ? (
            <span className="block text-ink-muted">{pick(row.match_explanation, locale)}</span>
          ) : null}
          {row.routes[0] ? (
            <span className="block text-xs">
              {t("suggestedFix", { route: pick(row.routes[0], locale) })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <FindingStatusBadge status={row.status} />,
    },
    {
      key: "open",
      header: tc("actions"),
      className: "print:hidden",
      cell: (row) => (
        <Link
          href={`/findings/${row.id}`}
          className="inline-flex items-center gap-1 font-medium whitespace-nowrap text-primary underline-offset-4 hover:underline"
        >
          {t("openFinding")}
          <span className="sr-only">
            : {row.rule_id} {row.student.display_name ?? ""}
          </span>
        </Link>
      ),
    },
  ];

  const activeRule = filters.ruleId ? ruleText(rules.data, filters.ruleId, locale) : null;
  const filterSummary = [
    filters.status.map((status) => tstatus(status)).join(", "),
    filters.severity.length > 0 ? filters.severity.map((s) => tsev(s)).join(", ") : null,
    filters.ruleId ? `${filters.ruleId}${activeRule ? ` · ${activeRule}` : ""}` : null,
    profileLabel(profiles.data, filters.profileKey, locale),
    filters.sectionId ? (sections.find((s) => s.id === filters.sectionId)?.label ?? null) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  // Counts by severity exactly as the API returns them (no zero rows invented).
  const bySeverity = SEVERITIES.filter(
    (severity) => (summary.data?.by_severity[severity] ?? 0) > 0,
  ).map((severity) => ({ severity, count: summary.data?.by_severity[severity] ?? 0 }));
  // One severity at a time (a link with several severity= parameters shows "All" here).
  const severityValue = filters.severity.length === 1 ? (filters.severity[0] ?? "") : "";
  const lastRun = formatDateTime(summary.data?.last_run?.finished_at ?? null);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: t("crumbHome"), href: "/" }, { label: t("title") }]}
        actions={
          <>
            {can("dq.findings.read") ? (
              <RunChecksDialog defaultProfile={filters.profileKey} />
            ) : null}
            <Button variant="secondary" onClick={() => window.print()}>
              {t("print")}
            </Button>
          </>
        }
      />
      <p className="hidden text-sm print:block" suppressHydrationWarning>
        {t("printedAt", { time: formatDateTime(new Date().toISOString()) ?? "" })} · {filterSummary}
      </p>

      <section aria-labelledby="dq-summary-heading" className="space-y-3">
        <h2 id="dq-summary-heading" className="sr-only">
          {t("summaryTitle")}
        </h2>
        {summary.isError ? (
          <Alert tone="warning">{t("summaryUnavailable")}</Alert>
        ) : (
          <>
            <div className="grid gap-4 md:grid-cols-3">
              <KpiCard
                label={t("summaryBlockers")}
                value={count(summary.data?.blockers)}
                unavailableLabel={tc("notAvailable")}
                aside={<SeverityBadge severity="blocker" />}
              />
              <KpiCard
                label={t("summaryWarnings")}
                value={count(summary.data?.warnings)}
                unavailableLabel={tc("notAvailable")}
              />
              <KpiCard
                label={t("summaryStudents")}
                value={count(summary.data?.students_with_blockers)}
                unavailableLabel={tc("notAvailable")}
              />
            </div>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-ink-muted">
              <p className="inline-flex flex-wrap items-center gap-2">
                <span>{t("summaryLastRun")}:</span>
                <Pill variant="date">{lastRun ?? t("neverRun")}</Pill>
              </p>
              {bySeverity.length > 0 ? (
                <p className="inline-flex flex-wrap items-center gap-2">
                  <span>{t("summaryBySeverityLabel")}</span>
                  {bySeverity.map((item) => (
                    <span key={item.severity} className="inline-flex items-center gap-1">
                      <SeverityBadge severity={item.severity} />
                      <span className="font-mono text-ink tabular-nums">{count(item.count)}</span>
                    </span>
                  ))}
                </p>
              ) : null}
            </div>
          </>
        )}
      </section>

      <Card title={t("filtersTitle")} className="print:hidden">
        <form method="get" className="space-y-5">
          {filters.studentId ? (
            <div className="flex flex-wrap items-center gap-3">
              <input type="hidden" name="student_id" value={filters.studentId} />
              <Pill variant="date" size="md">
                {t("oneStudentOnly")}
              </Pill>
              <Link href="/findings" className="text-sm text-primary underline">
                {t("allStudents")}
              </Link>
            </div>
          ) : null}
          <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
            <SegmentedControl
              name="severity"
              legend={t("filterSeverity")}
              legendVisible
              size="sm"
              defaultValue={severityValue}
              options={[
                { value: "", label: tc("all") },
                ...SEVERITIES.map((severity) => ({ value: severity, label: tsev(severity) })),
              ]}
            />
            <fieldset className="space-y-1">
              <legend className="mb-1 text-sm font-medium text-ink">{t("filterStatus")}</legend>
              <div className="flex min-h-10 flex-wrap items-center gap-x-4">
                {FINDING_STATUSES.map((status) => (
                  <Checkbox
                    key={status}
                    name="status"
                    value={status}
                    label={tstatus(status)}
                    defaultChecked={filters.status.includes(status)}
                  />
                ))}
              </div>
            </fieldset>
          </div>
          <div className="grid items-end gap-4 md:grid-cols-2 xl:grid-cols-4">
            <SelectField
              name="rule_id"
              label={t("filterRule")}
              defaultValue={filters.ruleId ?? ""}
              options={[
                { value: "", label: tc("all") },
                ...RULE_IDS.map((id) => {
                  const text = ruleText(rules.data, id, locale);
                  return { value: id, label: text ? `${id} · ${text}` : id };
                }),
              ]}
            />
            <SelectField
              name="profile_key"
              label={t("filterProfile")}
              defaultValue={filters.profileKey ?? ""}
              options={[
                { value: "", label: t("noProfile") },
                ...(profiles.data ?? []).map((profile) => ({
                  value: profile.key,
                  label: profileLabel(profiles.data, profile.key, locale) ?? profile.key,
                })),
              ]}
            />
            <SelectField
              name="section_id"
              label={t("filterSection")}
              defaultValue={filters.sectionId ?? ""}
              options={[
                { value: "", label: tc("all") },
                ...sections.map((section) => ({ value: section.id, label: section.label })),
              ]}
            />
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit">
                <Icon name="filter" className="size-4" />
                {tc("applyFilters")}
              </Button>
              <Link href="/findings/rules" className="text-sm text-primary underline">
                {t("rulesLink")}
              </Link>
            </div>
          </div>
        </form>
      </Card>

      {state.status !== "ready" ? (
        <DataTable
          caption={t("title")}
          captionHidden
          columns={columns}
          state={state}
          rowKey={(row) => row.id}
          emptyTitle={t("emptyTitle")}
        />
      ) : rows.length === 0 ? (
        <EmptyState icon="checkCircle" title={t("emptyTitle")} body={t("emptyBody")} />
      ) : (
        <>
          <Card
            title={t("blockersTitle", { count: blockers.length })}
            description={t("blockersHint")}
          >
            {blockers.length > 0 ? (
              <DataTable
                caption={t("blockersCaption")}
                captionHidden
                columns={columns}
                state={{ status: "ready", data: blockers }}
                rowKey={(row) => row.id}
                emptyTitle={t("noBlockers")}
              />
            ) : (
              <p className="text-sm text-ink-muted">{t("noBlockers")}</p>
            )}
          </Card>
          <Card title={t("warningsTitle", { count: others.length })}>
            {others.length > 0 ? (
              <DataTable
                caption={t("warningsCaption")}
                captionHidden
                columns={columns}
                state={{ status: "ready", data: others }}
                rowKey={(row) => row.id}
                emptyTitle={t("noWarnings")}
              />
            ) : (
              <p className="text-sm text-ink-muted">{t("noWarnings")}</p>
            )}
          </Card>
          {findings.hasNextPage ? (
            <div className="flex justify-center print:hidden">
              <Button
                variant="secondary"
                onClick={() => void findings.fetchNextPage()}
                disabled={findings.isFetchingNextPage}
              >
                {findings.isFetchingNextPage ? tc("loading") : t("showMore")}
              </Button>
            </div>
          ) : null}
          {findings.isFetchingNextPage ? <LoadingState label={tc("loading")} rows={1} /> : null}
          <p className="sr-only" role="status" aria-live="polite">
            {t("shownCount", { count: rows.length })}
          </p>
        </>
      )}
    </div>
  );
}
