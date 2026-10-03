"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Link } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import {
  INDICATORS,
  KEYS,
  PERM,
  ifMatch,
  useFlags,
  useSettings,
  useSummary,
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
                        className="min-h-10 w-20 rounded-md border border-border-control bg-surface px-2 font-mono text-ink"
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

/**
 * Students to follow up (US-1705, US-1706, US-1708): "My flags" for the owner, every flag of the
 * caller's scope (class teachers: their sections; the principal: the school), the counts behind
 * the M5 exit metric and the rules. Purpose-limited: educational follow-up and child safety only.
 */
export function FlagsScreen() {
  const t = useTranslations("insights");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(PERM.insightsRead);
  const [filters, setFilters] = useState<FlagFilters>({
    view: "mine",
    status: "active",
    indicator: "any",
    due: "any",
  });
  const list = useFlags(filters, allowed);
  const summary = useSummary(allowed);
  const settings = useSettings(allowed);

  return (
    <div className="space-y-6">
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
          <Card title={t("listTitle")}>
            <div className="mb-4 flex flex-wrap items-end gap-4">
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
            <LoadGate data={meLoaded ? list : { status: "loading" }}>
              {(page) =>
                page.data.length === 0 ? (
                  <EmptyState icon="shieldCheck" title={t("emptyTitle")} body={t("emptyBody")} />
                ) : (
                  <TableScroll label={t("listTitle")}>
                    <Table>
                      <THead>
                        <Tr>
                          <Th>{t("columns.student")}</Th>
                          <Th>{t("columns.why")}</Th>
                          <Th>{t("columns.due")}</Th>
                          <Th>{t("columns.owner")}</Th>
                          <Th>{t("columns.status")}</Th>
                        </Tr>
                      </THead>
                      <TBody>
                        {page.data.map((flag) => (
                          <Tr key={flag.id}>
                            <Td>
                              <Link
                                href={`/flags/${flag.id}`}
                                className="font-semibold break-words text-primary underline underline-offset-4"
                              >
                                {flag.student.full_name ?? t("unnamed")}
                              </Link>
                              <p className="text-sm text-ink-muted">
                                {flag.student.section_label ?? ""}
                              </p>
                            </Td>
                            <Td>
                              <p className="text-sm text-ink">{t(`indicator.${flag.indicator}`)}</p>
                              <p className="text-sm break-words text-ink-muted">
                                <FlagReason flag={flag} />
                              </p>
                            </Td>
                            <Td>
                              <span className="flex flex-wrap items-center gap-1">
                                <span className="font-mono">{formatDate(flag.due_on)}</span>
                                <OverduePill overdue={flag.overdue} />
                              </span>
                            </Td>
                            <Td>{flag.owner?.display_name ?? t("unassigned")}</Td>
                            <Td>
                              <FlagStatusPill status={flag.status} />
                            </Td>
                          </Tr>
                        ))}
                      </TBody>
                    </Table>
                  </TableScroll>
                )
              }
            </LoadGate>
          </Card>
          <LoadGate data={settings}>
            {(value) => <RulesCard settings={value} canManage={can(PERM.insightsManage)} />}
          </LoadGate>
        </>
      )}
    </div>
  );
}
