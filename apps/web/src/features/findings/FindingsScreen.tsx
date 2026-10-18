"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, type ReactNode } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { FilterChips, type FilterChip } from "@/components/ui/FilterChips";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { KpiCard } from "@/components/ui/StatCard";
import { Value } from "@/components/ui/Value";
import { Link, useRouter } from "@/i18n/navigation";
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
import {
  findingsHref,
  findingsQuery,
  hasCustomStatus,
  type FindingFilterKey,
  type FindingFilters,
} from "./filters";
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

/** One group of the list (blockers, warnings): an h2 that names the region, then the table. */
function FindingGroup({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <div className="space-y-1">
        <h2 id={headingId} className="text-lg font-semibold text-ink">
          {title}
        </h2>
        {hint ? <p className="text-sm text-ink-muted">{hint}</p> : null}
      </div>
      {children}
    </section>
  );
}

/**
 * Findings list (US-501, US-502; FR-DQ-006): summary counts, filters in the URL (the active
 * ones as removable chips), blockers separated from and shown before warnings, the
 * conflicting values (masked where sensitive) with their sources, the explanation in the
 * reader's language and the suggested correction. Prints on A4.
 */
export function FindingsScreen({ filters }: { filters: FindingFilters }) {
  const t = useTranslations("findings");
  const tc = useTranslations("common");
  const ttable = useTranslations("ui.table");
  const tsev = useTranslations("findings.severity");
  const tstatus = useTranslations("findings.status");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const can = useStaffCan();
  const router = useRouter();
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

  const columns: ColumnV2<Finding>[] = [
    {
      key: "severity",
      stack: "field",
      header: t("colSeverity"),
      cell: (row) => <SeverityBadge severity={row.severity} />,
    },
    {
      key: "student",
      stack: "title",
      header: t("colStudent"),
      hideable: false,
      cell: (row) => (
        <span className="block min-w-36">
          <span className="block font-semibold">
            <Value>{row.student.display_name}</Value>
          </span>
          {row.student.admission_no ? (
            <span className="block max-w-56 font-mono text-xs break-all text-ink-muted">
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
    {
      key: "values",
      header: t("colValues"),
      className: "min-w-44",
      cell: (row) => <FindingValues values={row.values} />,
    },
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
      stack: "actions",
      header: tc("actions"),
      hideable: false,
      className: "print:hidden",
      cell: (row) => (
        <Link
          href={`/findings/${row.id}`}
          className="inline-flex min-h-6 items-center gap-1 font-semibold whitespace-nowrap text-primary underline-offset-4 hover:underline"
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
  const ruleValue = filters.ruleId
    ? `${filters.ruleId}${activeRule ? ` · ${activeRule}` : ""}`
    : null;
  const profileValue = profileLabel(profiles.data, filters.profileKey, locale);
  const sectionValue = filters.sectionId
    ? (sections.find((s) => s.id === filters.sectionId)?.label ?? null)
    : null;
  const filterSummary = [
    filters.status.map((status) => tstatus(status)).join(", "),
    filters.severity.length > 0 ? filters.severity.map((s) => tsev(s)).join(", ") : null,
    ruleValue,
    profileValue,
    sectionValue,
  ]
    .filter(Boolean)
    .join(" · ");

  // The filters in the URL as chips; removing one opens the list without it (docs/17 §4.3).
  const chips: FilterChip[] = [
    ...(hasCustomStatus(filters)
      ? [
          {
            id: "status",
            label: t("filterStatus"),
            value: filters.status.map((status) => tstatus(status)).join(", "),
          },
        ]
      : []),
    ...(filters.severity.length > 0
      ? [
          {
            id: "severity",
            label: t("filterSeverity"),
            value: filters.severity.map((s) => tsev(s)).join(", "),
          },
        ]
      : []),
    ...(ruleValue ? [{ id: "rule_id", label: t("filterRule"), value: ruleValue }] : []),
    ...(filters.profileKey
      ? [
          {
            id: "profile_key",
            label: t("filterProfile"),
            value: profileValue ?? filters.profileKey,
          },
        ]
      : []),
    ...(filters.sectionId
      ? [{ id: "section_id", label: t("filterSection"), value: sectionValue ?? t("chipSection") }]
      : []),
    ...(filters.studentId
      ? [{ id: "student_id", label: t("colStudent"), value: t("chipOneStudent") }]
      : []),
  ];
  const removeChip = (id: string) => router.push(findingsHref(filters, id as FindingFilterKey));
  const clearFilters = () => router.push("/findings");

  // Counts by severity exactly as the API returns them (no zero rows invented).
  const bySeverity = SEVERITIES.filter(
    (severity) => (summary.data?.by_severity[severity] ?? 0) > 0,
  ).map((severity) => ({ severity, count: summary.data?.by_severity[severity] ?? 0 }));
  // One severity at a time (a link with several severity= parameters shows "All" here).
  const severityValue = filters.severity.length === 1 ? (filters.severity[0] ?? "") : "";
  const lastRun = formatDateTime(summary.data?.last_run?.finished_at ?? null);

  const groupTable = (id: string, caption: string, data: readonly Finding[], empty: string) => (
    <DataTableV2
      id={id}
      stacked
      caption={caption}
      captionHidden
      columns={columns}
      state={{ status: "ready", data }}
      rowKey={(row) => row.id}
      emptyTitle={empty}
    />
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: t("crumbHome"), href: "/" }, { label: t("title") }]}
        actions={
          <>
            {can("dq.findings.resolve") ? (
              <RunChecksDialog defaultProfile={filters.profileKey} />
            ) : null}
            <ButtonLink href="/findings/rules" variant="secondary">
              {t("rulesLink")}
            </ButtonLink>
            <Button variant="secondary" onClick={() => window.print()}>
              <Icon name="printer" className="size-4" />
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
            <div className="grid gap-3 md:grid-cols-3">
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
                      <span className="num text-ink">{count(item.count)}</span>
                    </span>
                  ))}
                </p>
              ) : null}
            </div>
          </>
        )}
      </section>

      <div
        className="min-w-0 rounded-xl border border-border bg-surface print:hidden"
        data-print="hide"
      >
        <form
          method="get"
          role="search"
          aria-label={t("filtersTitle")}
          className="space-y-4 px-3 py-3 sm:px-4"
        >
          {filters.studentId ? (
            <input type="hidden" name="student_id" value={filters.studentId} />
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
              <legend className="mb-1 text-sm font-semibold text-ink">{t("filterStatus")}</legend>
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
            <div>
              <Button type="submit" variant="secondary">
                <Icon name="filter" className="size-4" />
                {tc("applyFilters")}
              </Button>
            </div>
          </div>
        </form>
        <FilterChips
          chips={chips}
          onRemove={removeChip}
          onClear={clearFilters}
          className="border-t border-border px-3 py-2 sm:px-4"
        />
      </div>

      {state.status !== "ready" || rows.length === 0 ? (
        <DataTableV2
          id="findings"
          stacked
          caption={t("title")}
          captionHidden
          columns={columns}
          state={state}
          rowKey={(row) => row.id}
          onRetry={() => void findings.refetch()}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
          emptyAction={
            chips.length > 0 ? (
              <ButtonLink href="/findings" variant="secondary" size="sm">
                {ttable("clearFilters")}
              </ButtonLink>
            ) : undefined
          }
        />
      ) : (
        <>
          <FindingGroup
            title={t("blockersTitle", { count: blockers.length })}
            hint={t("blockersHint")}
          >
            {groupTable("findings-blockers", t("blockersCaption"), blockers, t("noBlockers"))}
          </FindingGroup>
          <FindingGroup title={t("warningsTitle", { count: others.length })}>
            {groupTable("findings-warnings", t("warningsCaption"), others, t("noWarnings"))}
          </FindingGroup>
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
