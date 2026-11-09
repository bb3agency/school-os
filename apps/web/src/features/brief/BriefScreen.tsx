"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { KpiCard } from "@/components/ui/StatCard";
import { Toggle } from "@/components/ui/Toggle";
import type { Locale } from "@/i18n/routing";
import { useApiMutation, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import {
  BRIEF_KEYS,
  BRIEF_READ,
  fetchBrief,
  fetchHistory,
  fetchPreferences,
  readyShare,
  savePreferences,
  signed,
  type BriefPreferences,
  type WeeklyBrief,
} from "./data";

/** A count, or "fewer than N" when the API hid a small cell (FR-BRF-009). */
function useCount(brief: WeeklyBrief) {
  const t = useTranslations("brief");
  const locale = useLocale() as Locale;
  return (value: number | null): string =>
    value === null
      ? t("fewerThan", { n: brief.small_cell_min })
      : (formatCount(value, locale) ?? "");
}

function Readiness({ brief }: { brief: WeeklyBrief }) {
  const t = useTranslations("brief.readiness");
  const show = useCount(brief);
  if (brief.readiness === null) return null;
  return (
    <Card className="space-y-3">
      <h2 className="text-lg font-semibold text-ink">{t("title")}</h2>
      {brief.readiness.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("none")}</p>
      ) : (
        <ul className="divide-y divide-border">
          {brief.readiness.map((line) => {
            const share = readyShare(line);
            return (
              <li
                key={line.profile_key}
                className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-3"
              >
                <span className="min-w-48 font-semibold break-words text-ink">{line.label}</span>
                <span className="num text-ink">
                  {t("ready", { ready: show(line.ready), students: show(line.students) })}
                  {share !== null ? ` (${share}%)` : ""}
                </span>
                {line.ready_change !== null ? (
                  <span className="text-sm text-ink-muted">
                    {t("change", { change: signed(line.ready_change) })}
                  </span>
                ) : null}
                <span className="w-full text-sm text-ink-muted">
                  {t("breakdown", {
                    parent: show(line.needs_parent),
                    school: show(line.needs_school),
                    blocked: show(line.blocked),
                  })}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function Deadlines({ brief }: { brief: WeeklyBrief }) {
  const t = useTranslations("brief.deadlines");
  if (brief.deadlines === null) return null;
  return (
    <Card className="space-y-3">
      <h2 className="text-lg font-semibold text-ink">
        {t("title", { days: brief.deadline_days })}
      </h2>
      {brief.deadlines.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("none")}</p>
      ) : (
        <ul className="divide-y divide-border">
          {brief.deadlines.map((d) => (
            <li key={d.event_key} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-3">
              <span className="min-w-48 font-semibold break-words text-ink">{d.label}</span>
              <span className="num text-ink">
                {t("closes", { date: formatDate(d.closes_on) ?? d.closes_on, days: d.days_left })}
              </span>
              <span className="text-sm text-ink-muted">
                {d.confirmed ? t("confirmed") : t("expected")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function MoneyAndAsk({ brief }: { brief: WeeklyBrief }) {
  const t = useTranslations("brief");
  const locale = useLocale() as Locale;
  const show = useCount(brief);
  if (brief.fees === null && brief.ask === null) return null;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {brief.fees ? (
        <Card className="space-y-2">
          <h2 className="text-lg font-semibold text-ink">{t("fees.title")}</h2>
          <p className="num text-ink">
            {t("fees.collected", {
              amount: formatInr(brief.fees.collected, locale) ?? brief.fees.collected,
              receipts: brief.fees.receipts,
            })}
          </p>
          <p className="num text-sm text-ink-muted">
            {t("fees.due", {
              balance: formatInr(brief.fees.balance, locale) ?? brief.fees.balance,
              overdue: formatInr(brief.fees.overdue, locale) ?? brief.fees.overdue,
              students: show(brief.fees.students_owing),
            })}
          </p>
        </Card>
      ) : null}
      {brief.ask ? (
        <Card className="space-y-2">
          <h2 className="text-lg font-semibold text-ink">{t("ask.title")}</h2>
          <p className="num text-ink">
            {brief.ask.included === null
              ? t("ask.used", { answers: brief.ask.answers })
              : t("ask.usedOf", { answers: brief.ask.answers, included: brief.ask.included })}
          </p>
        </Card>
      ) : null}
    </div>
  );
}

function Preferences() {
  const t = useTranslations("brief.preferences");
  const api = useBffClient("staff");
  const prefs = useQuery({
    queryKey: BRIEF_KEYS.preferences,
    queryFn: () => fetchPreferences(api),
    retry: false,
  });
  const save = useApiMutation(
    (next: { in_app: boolean; email: boolean; version: number }) =>
      savePreferences(api, next, next.version),
    { invalidate: [BRIEF_KEYS.preferences] },
  );
  const current: BriefPreferences | undefined = prefs.data;
  const change = (patch: Partial<Pick<BriefPreferences, "in_app" | "email">>) => {
    if (!current) return;
    save.mutate({
      in_app: patch.in_app ?? current.in_app,
      email: patch.email ?? current.email,
      version: current.version,
    });
  };
  return (
    <Card className="space-y-4 print:hidden">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold text-ink">{t("title")}</h2>
        <p className="text-sm text-ink-muted">{t("description")}</p>
      </div>
      {current ? (
        <div className="space-y-3">
          <Toggle
            label={t("inApp")}
            description={t("inAppHint")}
            checked={current.in_app || current.email}
            disabled={save.isPending || current.email}
            onCheckedChange={(on) => change({ in_app: on })}
          />
          <Toggle
            label={t("email")}
            description={current.email_available ? t("emailHint") : t("emailUnavailable")}
            checked={current.email}
            disabled={save.isPending || !current.email_available}
            onCheckedChange={(on) => change({ email: on })}
          />
        </div>
      ) : prefs.error ? (
        <ApiErrorAlert error={prefs.error} />
      ) : (
        <LoadingState label={t("loading")} />
      )}
      {save.error ? <ApiErrorAlert error={save.error} /> : null}
    </Card>
  );
}

/**
 * Weekly management brief (FA-05, FR-BRF-001..015): what the office did and what is due, as
 * counts only, for the owner and the principal. No names and no student-level data; small
 * student counts read "fewer than 5". Printable on A4 for management meetings.
 */
export function BriefScreen({ initialWeek }: { initialWeek: string | null }) {
  const t = useTranslations("brief");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(BRIEF_READ);
  const [week, setWeek] = useState<string | null>(initialWeek);
  const current = useQuery({
    queryKey: BRIEF_KEYS.week(week),
    queryFn: () => fetchBrief(api, week),
    enabled: allowed,
    retry: false,
  });
  const weeks = useQuery({
    queryKey: BRIEF_KEYS.history,
    queryFn: () => fetchHistory(api),
    enabled: allowed,
    retry: false,
  });

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        allowed && current.data ? (
          <Button variant="secondary" onClick={() => window.print()} className="print:hidden">
            {t("print")}
          </Button>
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

  const brief = current.data;
  const items = weeks.data?.items ?? [];
  const show = (value: number | null | undefined): string | null =>
    value === null || value === undefined ? null : formatCount(value, locale);
  return (
    <div className="space-y-5">
      {header}
      {items.length > 0 ? (
        <SelectField
          label={t("week")}
          className="max-w-sm print:hidden"
          value={week ?? brief?.id ?? items[0]?.id ?? ""}
          onChange={(event) => setWeek(event.target.value || null)}
          options={items.map((item) => ({
            value: item.id,
            label: t("weekOption", {
              from: formatDate(item.week_start) ?? item.week_start,
              to: formatDate(item.week_end) ?? item.week_end,
            }),
          }))}
        />
      ) : null}
      {current.error ? <ApiErrorAlert error={current.error} /> : null}
      {!brief ? (
        current.error ? null : (
          <LoadingState label={tc("loading")} />
        )
      ) : (
        <>
          <p className="text-sm text-ink-muted">
            {brief.stored
              ? t("period", {
                  from: formatDate(brief.week_start) ?? brief.week_start,
                  to: formatDate(brief.week_end) ?? brief.week_end,
                  asOf: formatDate(brief.as_of) ?? brief.as_of,
                })
              : t("preview", { asOf: formatDate(brief.as_of) ?? brief.as_of })}
          </p>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {brief.findings ? (
              <KpiCard
                label={t("kpi.blockers")}
                value={show(brief.findings.blockers)}
                unavailableLabel={tc("notAvailable")}
                comparison={t("kpi.warnings", { count: brief.findings.warnings })}
                href="/findings"
              />
            ) : null}
            {brief.changes_waiting !== null ? (
              <KpiCard
                label={t("kpi.waiting")}
                value={show(brief.changes_waiting)}
                unavailableLabel={tc("notAvailable")}
                href="/change-requests"
              />
            ) : null}
            {brief.certificates_issued !== null ? (
              <KpiCard
                label={t("kpi.certificates")}
                value={show(brief.certificates_issued)}
                unavailableLabel={tc("notAvailable")}
                href="/certificates"
              />
            ) : null}
            {brief.deadlines !== null ? (
              <KpiCard
                label={t("kpi.deadlines", { days: brief.deadline_days })}
                value={show(brief.deadlines.length)}
                unavailableLabel={tc("notAvailable")}
                href="/findings/calendar"
              />
            ) : null}
          </div>
          <Readiness brief={brief} />
          <Deadlines brief={brief} />
          <MoneyAndAsk brief={brief} />
          <p className="text-xs text-ink-subtle">{t("footnote", { n: brief.small_cell_min })}</p>
        </>
      )}
      <Preferences />
    </div>
  );
}
