"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Pill } from "@/components/ui/Badge";
import { type FilterChip } from "@/components/ui/FilterChips";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Pager, useCursorStack } from "@/features/students/paging";
import { Link } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import type { Loadable } from "@/lib/loadable";
import {
  INDICATORS,
  KEYS,
  PERM,
  ifMatch,
  useFlags,
  useSettings,
  useSummary,
  type Flag,
  type FlagFilters,
  type Settings,
  type Summary,
} from "./data";
import {
  FlagReason,
  FlagStatusPill,
  LoadGate,
  OverduePill,
  ProblemList,
  PurposeNote,
} from "./parts";

/** Counts only, for my scope (FR-EW-015): the M5 exit metric is "acted on by the due date". */
function SummaryCards({ summary }: { summary: Summary }) {
  const t = useTranslations("insights.summary");
  const acted =
    summary.raised === 0 ? null : Math.round((summary.actioned_on_time * 100) / summary.raised);
  return (
    <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label={t("raised")}
        value={String(summary.raised)}
        unavailableLabel={t("none")}
        hint={t("since", { date: formatDate(summary.since) ?? summary.since })}
      />
      <StatCard
        label={t("onTime")}
        value={acted === null ? null : `${acted}%`}
        unavailableLabel={t("none")}
        hint={t("onTimeHint", { count: summary.actioned_on_time })}
      />
      <StatCard label={t("overdue")} value={String(summary.overdue)} unavailableLabel={t("none")} />
      <StatCard label={t("open")} value={String(summary.open)} unavailableLabel={t("none")} />
    </dl>
  );
}

/** How flags are raised; the principal may tune thresholds within bounds (FR-EW-014). */
function RulesCard({ settings, canManage }: { settings: Settings; canManage: boolean }) {
  const t = useTranslations("insights.rules");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState(false);
  const [pending, setPending] = useState(false);
  const ruleName = (key: string) => translateOr(t, `names.${key}`, "names.other");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const rules: Record<string, { threshold: number; enabled?: boolean }> = {};
    for (const rule of settings.rules) {
      const threshold = Number(form.get(`${rule.key}.threshold`));
      rules[rule.key] = {
        threshold,
        ...(rule.can_disable ? { enabled: form.get(`${rule.key}.enabled`) === "on" } : {}),
      };
    }
    setPending(true);
    setError(undefined);
    setSaved(false);
    try {
      await unwrap(
        api.PUT("/api/v1/insights/settings", {
          headers: { "If-Match": ifMatch(settings.version) },
          body: { rules },
        }),
      );
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: KEYS.settings });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card title={t("title")} description={t("description", { days: settings.due_days })}>
      <form onSubmit={(event) => void submit(event)} noValidate className="space-y-3">
        <TableScroll label={t("title")}>
          <Table>
            <THead>
              <Tr>
                <Th>{t("rule")}</Th>
                <Th>{t("threshold")}</Th>
                <Th>{t("enabled")}</Th>
              </Tr>
            </THead>
            <TBody>
              {settings.rules.map((rule) => (
                <Tr key={rule.key}>
                  <Td>
                    <p className="font-semibold text-ink">{ruleName(rule.key)}</p>
                    <p className="text-sm text-ink-muted">
                      {translateOr(t, `explain.${rule.key}`, "explain.other", {
                        threshold: rule.threshold,
                        window: rule.window ?? 30,
                      })}
                    </p>
                  </Td>
                  <Td>
                    {canManage ? (
                      <input
                        name={`${rule.key}.threshold`}
                        type="number"
                        min={rule.min}
                        max={rule.max}
                        defaultValue={rule.threshold}
                        aria-label={t("thresholdFor", {
                          rule: ruleName(rule.key),
                          min: rule.min,
                          max: rule.max,
                        })}
                        inputMode="numeric"
                        className="min-h-10 w-20 rounded-md border border-border-control bg-surface px-2 font-mono text-base text-ink pointer-coarse:min-h-11"
                      />
                    ) : (
                      <span className="font-mono">{rule.threshold}</span>
                    )}
                    <p className="text-xs text-ink-subtle">
                      {t("bounds", { min: rule.min, max: rule.max })}
                    </p>
                  </Td>
                  <Td>
                    {canManage && rule.can_disable ? (
                      <label className="inline-flex min-h-6 items-center gap-2 text-sm text-ink">
                        <input
                          type="checkbox"
                          name={`${rule.key}.enabled`}
                          defaultChecked={rule.enabled}
                          aria-label={t("enabledFor", { rule: ruleName(rule.key) })}
                        />
                        {t("on")}
                      </label>
                    ) : (
                      <span className="text-sm text-ink">
                        {rule.enabled ? t("on") : t("off")}
                        {!rule.can_disable ? ` · ${t("alwaysOn")}` : ""}
                      </span>
                    )}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableScroll>
        {canManage ? (
          <>
            <p className="text-xs text-ink-subtle">{t("stepUp")}</p>
            <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
              {t("save")}
            </Button>
          </>
        ) : null}
        {saved ? (
          <Alert tone="success" live>
            {t("saved")}
          </Alert>
        ) : null}
        <ApiErrorAlert error={error} />
        <ProblemList error={error} />
      </form>
    </Card>
  );
}

const DEFAULT_FILTERS: FlagFilters = {
  view: "mine",
  status: "active",
  indicator: "any",
  due: "any",
};

/**
 * Students to follow up (US-1705, US-1706, US-1708): "My flags" for the owner, every flag of the
 * caller's scope (class teachers: their sections; the principal: the school), the counts behind
 * the M5 exit metric and the rules. Purpose-limited: educational follow-up and child safety only.
 */
export function FlagsScreen() {
  const t = useTranslations("insights");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const queryClient = useQueryClient();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(PERM.insightsRead);
  const [filters, setFilterState] = useState<FlagFilters>(DEFAULT_FILTERS);
  // Cursor paging (docs/09 §2): a page holds 100 flags; new filters start at the first page.
  const pages = useCursorStack();
  const setFilters = (next: FlagFilters) => {
    pages.reset();
    setFilterState(next);
  };
  const list = useFlags(filters, allowed, pages.cursor);
  const summary = useSummary(allowed);
  const settings = useSettings(allowed);
  const shown = meLoaded ? list : ({ status: "loading" } as const);
  const rows: Loadable<readonly Flag[]> =
    shown.status === "ready" ? { status: "ready", data: shown.data.data } : shown;
  const nextCursor = shown.status === "ready" ? (shown.data.next_cursor ?? null) : null;

  // Active filters (other than the defaults) as chips; removing one sets it back.
  const chips: FilterChip[] = [
    ...(filters.status !== DEFAULT_FILTERS.status
      ? [{ id: "status", label: t("statusLabel"), value: t(`filter.${filters.status}`) }]
      : []),
    ...(filters.indicator !== "any"
      ? [
          {
            id: "indicator",
            label: t("indicatorLabel"),
            value: t(`indicator.${filters.indicator}`),
          },
        ]
      : []),
    ...(filters.due !== "any" ? [{ id: "due", label: t("dueLabel"), value: t("overdue") }] : []),
  ];
  const removeChip = (id: string) => {
    if (id === "status" || id === "indicator" || id === "due") {
      setFilters({ ...filters, [id]: DEFAULT_FILTERS[id] });
    }
  };

  const columns: ColumnV2<Flag>[] = [
    {
      key: "student",
      header: t("columns.student"),
      stack: "title",
      cell: (flag) => (
        <span className="flex flex-col gap-0.5">
          <Link
            href={`/flags/${flag.id}`}
            className="font-semibold break-words text-primary underline-offset-4 hover:underline"
          >
            {flag.student.full_name ?? t("unnamed")}
          </Link>
          {flag.student.section_label ? (
            <span className="text-sm text-ink-muted">{flag.student.section_label}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "why",
      header: t("columns.why"),
      stack: "wide",
      cell: (flag) => (
        <span className="flex flex-col items-start gap-1">
          <Pill variant="tag" size="sm" icon={null}>
            {t(`indicator.${flag.indicator}`)}
          </Pill>
          <span className="text-sm break-words text-ink-muted">
            <FlagReason flag={flag} />
          </span>
        </span>
      ),
    },
    {
      key: "due",
      header: t("columns.due"),
      cell: (flag) => (
        <span className="flex flex-wrap items-center gap-1">
          <span className="num">{formatDate(flag.due_on)}</span>
          <OverduePill overdue={flag.overdue} />
        </span>
      ),
    },
    {
      key: "owner",
      header: t("columns.owner"),
      cell: (flag) => flag.owner?.display_name ?? t("unassigned"),
    },
    {
      key: "status",
      header: t("columns.status"),
      cell: (flag) => <FlagStatusPill status={flag.status} />,
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {meLoaded && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <>
          <PurposeNote />
          <LoadGate data={summary}>{(value) => <SummaryCards summary={value} />}</LoadGate>
          <DataTableV2
            id="flags"
            caption={t("listTitle")}
            columns={columns}
            state={rows}
            rowKey={(flag) => flag.id}
            stacked
            toolbar={
              <div className="flex flex-wrap items-end gap-3">
                <SegmentedControl
                  legend={t("viewLabel")}
                  legendVisible
                  size="sm"
                  value={filters.view}
                  onValueChange={(view) =>
                    setFilters({ ...filters, view: view as FlagFilters["view"] })
                  }
                  options={[
                    { value: "mine", label: t("view.mine") },
                    { value: "all", label: t("view.all") },
                  ]}
                />
                <SelectField
                  label={t("statusLabel")}
                  value={filters.status}
                  onChange={(event) =>
                    setFilters({ ...filters, status: event.target.value as FlagFilters["status"] })
                  }
                  options={(["active", "open", "in_progress", "closed"] as const).map((value) => ({
                    value,
                    label: t(`filter.${value}`),
                  }))}
                />
                <SelectField
                  label={t("indicatorLabel")}
                  value={filters.indicator}
                  onChange={(event) =>
                    setFilters({
                      ...filters,
                      indicator: event.target.value as FlagFilters["indicator"],
                    })
                  }
                  options={[
                    { value: "any", label: t("filter.any") },
                    ...INDICATORS.map((value) => ({ value, label: t(`indicator.${value}`) })),
                  ]}
                />
                <SelectField
                  label={t("dueLabel")}
                  value={filters.due}
                  onChange={(event) =>
                    setFilters({ ...filters, due: event.target.value as FlagFilters["due"] })
                  }
                  options={[
                    { value: "any", label: t("filter.any") },
                    { value: "overdue", label: t("overdue") },
                  ]}
                />
              </div>
            }
            chips={chips}
            onRemoveChip={removeChip}
            onClearFilters={() => setFilters({ ...DEFAULT_FILTERS, view: filters.view })}
            emptyTitle={t("emptyTitle")}
            emptyBody={t("emptyBody")}
            onRetry={() => void queryClient.invalidateQueries({ queryKey: KEYS.all })}
            footer={
              rows.status === "ready" ? (
                <Pager
                  label={t("pagesLabel")}
                  page={pages.page}
                  onPrevious={pages.hasPrevious ? pages.previous : undefined}
                  onNext={nextCursor ? () => pages.next(nextCursor) : undefined}
                />
              ) : null
            }
          />
          <LoadGate data={settings}>
            {(value) => <RulesCard settings={value} canManage={can(PERM.insightsManage)} />}
          </LoadGate>
        </>
      )}
    </div>
  );
}
