"use client";

import type { components } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon, type IconName } from "@/components/ui/Icon";
import { LoadFade } from "@/components/ui/LoadFade";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { KpiCard } from "@/components/ui/StatCard";
import { Timeline, type TimelineItem } from "@/components/ui/Timeline";
import { ImportStatusBadge, SourceName } from "@/features/imports/ImportsScreen";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatCount, formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

type Schemas = components["schemas"];
type DqSummary = Schemas["SummaryOut"];
type ImportSummary = Schemas["ImportSummary"];

/** Permissions of the screens the home links to (authz/permissions.yaml; UX only). */
export const HOME_PERM = {
  findings: "dq.findings.read",
  changes: ["student.identity_change.request", "student.identity_change.approve"],
  imports: "import.run",
  ask: "kb.ask",
  users: "user.manage",
} as const;

/** Pending change requests are counted from one page; more than this reads "200+". */
const PENDING_PAGE = 200;
const RECENT_IMPORTS = 3;

type StepId = "structure" | "users" | "import" | "checks";

export interface PendingCount {
  count: number;
  /** The API has more pending requests than one page holds. */
  more: boolean;
}

export interface HomeData {
  /** Display name from GET /me, when it has loaded. */
  name: string | null;
  permissions: readonly string[] | null;
  /** GET /dq/summary (unresolved findings), only with dq.findings.read. */
  checks: Loadable<DqSummary> | null;
  /** GET /change-requests?status=pending, only with a change-request permission. */
  pending: Loadable<PendingCount> | null;
  /** GET /imports (newest first), only with import.run. */
  imports: Loadable<readonly ImportSummary[]> | null;
}

/** A number the API returned, or null ("—") while loading or when it failed. */
function numberOf(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function WorkCard({
  title,
  icon,
  loading,
  children,
  action,
}: {
  title: string;
  icon: IconName;
  /** The card's numbers are still loading (the text fades in when they arrive). */
  loading: boolean;
  children: ReactNode;
  action: ReactNode;
}) {
  return (
    <Card
      title={
        <span className="inline-flex items-center gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary">
            <Icon name={icon} className="size-4.5" />
          </span>
          {title}
        </span>
      }
      className="flex flex-col"
    >
      <div className="flex-1 space-y-3 text-sm text-ink-muted">
        <LoadFade loading={loading} className="space-y-3">
          {children}
        </LoadFade>
      </div>
      <div className="mt-5" data-print="hide">
        {action}
      </div>
    </Card>
  );
}

/**
 * School home (dashboard): a greeting, a KPI row and "work waiting" cards built only from
 * numbers existing endpoints return (dq summary, pending change requests, recent imports),
 * each shown only to people who can open that screen. A school without imports gets the
 * getting-started steps. Nothing is estimated: a number that did not load shows "—".
 */
export function HomeView({ data }: { data: HomeData }) {
  const t = useTranslations("school.home");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const count = (value: number | null) => (value === null ? null : formatCount(value, locale));
  const can = (permission: string | readonly string[]) => {
    if (!data.permissions) return false;
    const wanted = typeof permission === "string" ? [permission] : permission;
    return wanted.some((item) => data.permissions?.includes(item));
  };

  const summary = data.checks?.status === "ready" ? data.checks.data : null;
  const blockers = numberOf(summary?.blockers);
  const warnings = numberOf(summary?.warnings);
  const affected = numberOf(summary?.students_with_blockers);
  const pending = data.pending?.status === "ready" ? data.pending.data : null;
  const pendingText = pending
    ? `${count(pending.count) ?? pending.count}${pending.more ? "+" : ""}`
    : null;
  const imports = data.imports?.status === "ready" ? data.imports.data : null;
  const lastRun = summary?.last_run?.finished_at ?? null;

  const kpis: ReactNode[] = [];
  if (data.checks) {
    kpis.push(
      <KpiCard
        key="blockers"
        label={t("kpi.blockers")}
        value={count(blockers)}
        unavailableLabel={tc("notAvailable")}
        comparison={t("kpi.blockersNote")}
      />,
      <KpiCard
        key="warnings"
        label={t("kpi.warnings")}
        value={count(warnings)}
        unavailableLabel={tc("notAvailable")}
        comparison={t("kpi.warningsNote")}
      />,
      <KpiCard
        key="affected"
        label={t("kpi.studentsAffected")}
        value={count(affected)}
        unavailableLabel={tc("notAvailable")}
        comparison={t("kpi.studentsAffectedNote")}
      />,
    );
  }
  if (data.pending) {
    kpis.push(
      <KpiCard
        key="pending"
        label={t("changesWaiting")}
        value={pendingText}
        unavailableLabel={tc("notAvailable")}
        comparison={t("kpi.pendingNote")}
      />,
    );
  }

  const work: ReactNode[] = [];
  if (data.checks) {
    work.push(
      <WorkCard
        key="checks"
        title={t("checksToReview")}
        icon="shieldCheck"
        loading={data.checks.status === "loading"}
        action={
          <ButtonLink href="/findings" size="sm" variant="secondary">
            {t("work.checksAction")}
            <Icon name="arrowRight" className="size-4" />
          </ButtonLink>
        }
      >
        {data.checks.status === "loading" ? <LoadingState label={tc("loading")} rows={2} /> : null}
        {data.checks.status === "error" || data.checks.status === "unavailable" ? (
          <p>{t("work.loadError")}</p>
        ) : null}
        {summary ? (
          blockers === 0 && warnings === 0 ? (
            <p>{t("work.checksNone")}</p>
          ) : (
            <p>
              {t("work.checksBody", {
                blockers: count(blockers) ?? "—",
                warnings: count(warnings) ?? "—",
              })}
            </p>
          )
        ) : null}
        {summary ? (
          <p className="text-ink-subtle">
            {lastRun
              ? t("work.lastRun", { time: formatDateTime(lastRun) ?? "" })
              : t("work.neverRun")}
          </p>
        ) : null}
      </WorkCard>,
    );
  }
  if (data.pending) {
    work.push(
      <WorkCard
        key="changes"
        title={t("changesWaiting")}
        icon="clipboard"
        loading={data.pending.status === "loading"}
        action={
          <ButtonLink href="/change-requests" size="sm" variant="secondary">
            {t("work.changesAction")}
            <Icon name="arrowRight" className="size-4" />
          </ButtonLink>
        }
      >
        {data.pending.status === "loading" ? <LoadingState label={tc("loading")} rows={2} /> : null}
        {data.pending.status === "error" || data.pending.status === "unavailable" ? (
          <p>{t("work.loadError")}</p>
        ) : null}
        {pending ? (
          <p>
            {pending.count === 0
              ? t("work.changesNone")
              : t("work.changesBody", { count: pending.count, shown: pendingText ?? "" })}
          </p>
        ) : null}
        {pending ? <p className="text-ink-subtle">{t("work.changesNote")}</p> : null}
      </WorkCard>,
    );
  }
  if (data.imports) {
    work.push(
      <WorkCard
        key="imports"
        title={t("recentImports")}
        icon="upload"
        loading={data.imports.status === "loading"}
        action={
          <ButtonLink href="/imports" size="sm" variant="secondary">
            {t("work.importsAction")}
            <Icon name="arrowRight" className="size-4" />
          </ButtonLink>
        }
      >
        {data.imports.status === "loading" ? <LoadingState label={tc("loading")} rows={2} /> : null}
        {data.imports.status === "error" || data.imports.status === "unavailable" ? (
          <p>{t("work.loadError")}</p>
        ) : null}
        {imports && imports.length === 0 ? <p>{t("work.importsNone")}</p> : null}
        {imports && imports.length > 0 ? (
          <ul className="divide-y divide-border" aria-label={t("recentImports")}>
            {imports.slice(0, RECENT_IMPORTS).map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                <Link
                  href={`/imports/${item.id}`}
                  className="font-medium text-primary underline-offset-4 hover:underline"
                >
                  <SourceName source={item.source} />
                </Link>
                <ImportStatusBadge status={item.status} />
                <span className="w-full font-mono text-xs text-ink-subtle">
                  {formatDateTime(item.created_at)}
                  {" · "}
                  {t("work.rows", { count: item.row_count })}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </WorkCard>,
    );
  }

  // Getting started: shown until we know the school has imported records.
  const isNew = !imports || imports.length === 0;
  const stepList: ReadonlyArray<{ id: StepId; href: string; perm: string | null }> = [
    // School structure is open to every member (read); editing it is checked on that screen.
    { id: "structure", href: "/settings/structure", perm: null },
    { id: "users", href: "/settings/users", perm: HOME_PERM.users },
    { id: "import", href: "/imports", perm: HOME_PERM.imports },
    { id: "checks", href: "/findings", perm: HOME_PERM.findings },
  ];
  const steps: TimelineItem[] = stepList.map((step, index): TimelineItem => ({
    id: step.id,
    status: index === 0 ? "current" : "pending",
    statusLabel: index === 0 ? t("steps.startHere") : t("steps.next"),
    title:
      step.perm === null || can(step.perm) ? (
        <Link
          href={step.href}
          className="text-primary underline underline-offset-4 hover:no-underline"
        >
          {t(`steps.${step.id}.title`)}
        </Link>
      ) : (
        t(`steps.${step.id}.title`)
      ),
    body: t(`steps.${step.id}.body`),
  }));

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        eyebrow={t("eyebrow")}
        description={data.name ? t("greeting", { name: data.name }) : t("description")}
        actions={
          can(HOME_PERM.ask) ? (
            <ButtonLink href="/ask" variant="secondary">
              <Icon name="sparkles" className="size-4" />
              {t("askAction")}
            </ButtonLink>
          ) : null
        }
      />
      {kpis.length > 0 ? (
        <section aria-label={t("kpi.label")}>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{kpis}</div>
        </section>
      ) : null}
      {work.length > 0 ? (
        <section aria-labelledby="home-work" className="space-y-3">
          <h2 id="home-work" className="text-lg font-semibold text-ink">
            {t("work.title")}
          </h2>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{work}</div>
        </section>
      ) : null}
      {isNew ? (
        <Card title={t("gettingStartedTitle")} description={t("gettingStartedBody")}>
          <Timeline items={steps} label={t("steps.label")} />
        </Card>
      ) : null}
      {data.permissions && work.length === 0 && !isNew ? (
        <EmptyState title={t("nothingTitle")} body={t("nothingBody")} icon="checkCircle" />
      ) : null}
    </div>
  );
}

/** Loads what the home shows, each part only with the permission its screen needs. */
export function HomeScreen() {
  const api = useBffClient("staff");
  const me = useStaffMeQuery();
  const permissions = me.data?.permissions ?? null;
  const has = (permission: string | readonly string[]) => {
    const wanted = typeof permission === "string" ? [permission] : permission;
    return Boolean(permissions && wanted.some((item) => permissions.includes(item)));
  };
  const canChecks = has(HOME_PERM.findings);
  const canChanges = has(HOME_PERM.changes);
  const canImports = has(HOME_PERM.imports);

  const checks = useApiQuery(
    ["staff", "home", "dq-summary"],
    () => unwrap(api.GET("/api/v1/dq/summary")),
    { enabled: canChecks },
  );
  const pending = useApiQuery(
    ["staff", "home", "pending-changes"],
    async (): Promise<PendingCount> => {
      const result = await unwrap(
        api.GET("/api/v1/change-requests", {
          params: { query: { status: "pending", limit: PENDING_PAGE } },
        }),
      );
      return { count: result.data.length, more: Boolean(result.next_cursor) };
    },
    { enabled: canChanges },
  );
  const imports = useApiQuery(
    ["staff", "home", "recent-imports"],
    async () =>
      (await unwrap(api.GET("/api/v1/imports", { params: { query: { limit: RECENT_IMPORTS } } })))
        .data,
    { enabled: canImports },
  );

  return (
    <HomeView
      data={{
        name: me.data?.display_name?.trim() || null,
        permissions,
        checks: canChecks ? checks : null,
        pending: canChanges ? pending : null,
        imports: canImports ? imports : null,
      }}
    />
  );
}
