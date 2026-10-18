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
import { KEYS as FEE_KEYS, isFeesOff } from "@/features/fees/data";
import { ImportStatusBadge, SourceName } from "@/features/imports/ImportsScreen";
import { READINESS_KEYS } from "@/features/readiness/data";
import { DQ_KEYS } from "@/features/findings/data";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffMeQuery } from "@/lib/bff/staff-me";
import { cn } from "@/lib/cn";
import { formatCount, formatDateTime, formatInr } from "@/lib/format";
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
  addStudent: "student.create",
  attendance: "attendance.record",
  marks: "marks.record",
  feeCollect: "fee.collect",
  feeRead: "finance.read",
  feeApprove: ["fee.concession.approve", "fee.receipt.cancel", "fee.refund.approve"],
} as const;

/** Pending change requests are counted from one page; more than this reads "200+". */
const PENDING_PAGE = 200;
const RECENT_IMPORTS = 3;

type StepId = "structure" | "users" | "import" | "checks";

/**
 * Which "Today" the person gets (research report §2, §7.4). One page; the role only orders
 * the sections and picks the one primary action. Every section is still shown only with the
 * permission of the screen behind it.
 */
export type HomeRole = "office" | "accountant" | "teacher" | "principal";

/** System role keys (authz/roles.yaml) → the home variant; custom roles get the office home. */
export function homeRoleOf(roles: readonly string[] | null | undefined): HomeRole {
  const has = (...keys: string[]) => keys.some((key) => roles?.includes(key));
  if (has("principal", "owner")) return "principal";
  if (has("office_admin", "office_staff", "exam_coordinator")) return "office";
  if (has("accountant")) return "accountant";
  if (has("class_teacher", "teacher")) return "teacher";
  return "office";
}

export interface PendingCount {
  count: number;
  /** The API has more pending requests than one page holds. */
  more: boolean;
}

/** Today's money at the counter (GET /fees/status, then GET /fees/reports/daily). */
export interface FeesToday {
  date: string;
  receiptsTotal: string;
  netTotal: string;
  cashToHandOver: string;
  receiptCount: number;
  closed: boolean;
}

/** Portal readiness of the first readiness profile (GET /dq/profiles, /dq/readiness/{key}). */
export interface ReadinessToday {
  profileKey: string;
  label: string;
  ready: number;
  students: number;
}

export interface HomeData {
  /** Display name from GET /me, when it has loaded. */
  name: string | null;
  permissions: readonly string[] | null;
  /** Role keys from GET /me (orders the sections; never grants anything). */
  roles?: readonly string[] | null;
  /** GET /dq/summary (unresolved findings), only with dq.findings.read. */
  checks: Loadable<DqSummary> | null;
  /** GET /change-requests?status=pending, only with a change-request permission. */
  pending: Loadable<PendingCount> | null;
  /** GET /imports (newest first), only with import.run. */
  imports: Loadable<readonly ImportSummary[]> | null;
  /** Today's fee totals, only with finance.read while fees are on for the school. */
  fees?: Loadable<FeesToday> | null;
  /** Fee requests this person can decide, only for fee approvers while fees are on. */
  feeApprovals?: Loadable<number> | null;
  /** Readiness for the first portal profile, only for principals with dq.findings.read. */
  readiness?: Loadable<ReadinessToday | null> | null;
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
  /** The row's numbers are still loading (the text fades in when they arrive). */
  loading: boolean;
  children: ReactNode;
  action: ReactNode;
}) {
  // One row of "Waiting for you" (report §7.4): icon, what it is, the numbers, one action.
  return (
    <li className="flex flex-wrap items-start gap-x-4 gap-y-3 px-4 py-4 sm:px-5">
      <span
        aria-hidden="true"
        className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-surface-muted text-ink-muted"
      >
        <Icon name={icon} className="size-4.5" />
      </span>
      <div className="min-w-0 flex-[1_1_16rem] space-y-1.5 text-sm text-ink-muted">
        <h3 className="text-base font-semibold text-ink">{title}</h3>
        <LoadFade loading={loading} className="space-y-1.5">
          {children}
        </LoadFade>
      </div>
      <div className="shrink-0" data-print="hide">
        {action}
      </div>
    </li>
  );
}

/** A large tap target for a teacher's daily job (attendance, marks): icon, title, one line. */
function TaskLink({
  href,
  icon,
  title,
  body,
  primary = false,
}: {
  href: string;
  icon: IconName;
  title: string;
  body: string;
  primary?: boolean;
}) {
  return (
    <li className="min-w-0">
      <Link
        href={href}
        className={cn(
          "pressable flex min-h-20 items-center gap-4 rounded-xl border px-4 py-4 sm:px-5",
          primary
            ? "border-action bg-action text-on-action hover:bg-action-hover"
            : "border-border bg-surface text-ink hover:bg-surface-hover",
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "flex size-10 shrink-0 items-center justify-center rounded-lg",
            primary ? "bg-white/15" : "bg-surface-muted text-ink-muted",
          )}
        >
          <Icon name={icon} className="size-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-base font-semibold">{title}</span>
          <span className={cn("block text-sm", primary ? "opacity-85" : "text-ink-muted")}>
            {body}
          </span>
        </span>
        <Icon name="arrowRight" className="size-5 shrink-0" />
      </Link>
    </li>
  );
}

/**
 * School home, "Today" (report §2, §7.4): a greeting with the role's one primary action, at
 * most four KPI cards (each linking to the list behind it), then "Waiting for you", built only
 * from numbers existing endpoints return (dq summary, pending change requests, recent imports,
 * today's fee totals, fee approvals, portal readiness), each shown only to people who can open
 * that screen. The role (office clerk, accountant, teacher, principal) orders the sections and
 * picks the primary action; it never shows more. A school without imports gets the
 * getting-started steps. Nothing is estimated: a number that did not load shows "—".
 */
export function HomeView({ data }: { data: HomeData }) {
  const t = useTranslations("school.home");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const role = homeRoleOf(data.roles);
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
  const fees = data.fees?.status === "ready" ? data.fees.data : null;
  const feeApprovals = data.feeApprovals?.status === "ready" ? data.feeApprovals.data : null;
  const readiness = data.readiness?.status === "ready" ? data.readiness.data : null;
  const readinessPercent =
    readiness && readiness.students > 0
      ? Math.round((readiness.ready / readiness.students) * 100)
      : null;

  // ---- KPI cards (at most four, in the role's order) ----
  const kpi: Record<string, ReactNode> = {};
  if (data.checks) {
    kpi.blockers = (
      <KpiCard
        key="blockers"
        href="/findings"
        label={t("kpi.blockers")}
        value={count(blockers)}
        unavailableLabel={tc("notAvailable")}
        comparison={t("kpi.blockersNote")}
      />
    );
    kpi.warnings = (
      <KpiCard
        key="warnings"
        href="/findings"
        label={t("kpi.warnings")}
        value={count(warnings)}
        unavailableLabel={tc("notAvailable")}
        comparison={t("kpi.warningsNote")}
      />
    );
    kpi.affected = (
      <KpiCard
        key="affected"
        label={t("kpi.studentsAffected")}
        value={count(affected)}
        unavailableLabel={tc("notAvailable")}
        comparison={t("kpi.studentsAffectedNote")}
      />
    );
  }
  if (data.pending) {
    kpi.pending = (
      <KpiCard
        key="pending"
        href="/change-requests"
        label={t("changesWaiting")}
        value={pendingText}
        unavailableLabel={tc("notAvailable")}
        comparison={t("kpi.pendingNote")}
      />
    );
  }
  if (data.fees) {
    kpi.feesNet = (
      <KpiCard
        key="feesNet"
        href="/fees/daily"
        label={t("kpi.feesToday")}
        value={fees ? formatInr(fees.netTotal, locale) : null}
        unavailableLabel={tc("notAvailable")}
        comparison={
          fees ? t("kpi.feesTodayNote", { count: fees.receiptCount }) : t("kpi.feesTodayEmpty")
        }
      />
    );
    kpi.feesCash = (
      <KpiCard
        key="feesCash"
        href="/fees/daily"
        label={t("kpi.cashToHandOver")}
        value={fees ? formatInr(fees.cashToHandOver, locale) : null}
        unavailableLabel={tc("notAvailable")}
        comparison={t("kpi.cashToHandOverNote")}
      />
    );
  }
  if (data.feeApprovals) {
    kpi.feeApprovals = (
      <KpiCard
        key="feeApprovals"
        href="/fees/approvals"
        label={t("kpi.feeApprovals")}
        value={count(feeApprovals)}
        unavailableLabel={tc("notAvailable")}
        comparison={t("kpi.feeApprovalsNote")}
      />
    );
  }
  if (data.readiness && data.readiness.status !== "ready") {
    kpi.readiness = (
      <KpiCard
        key="readiness"
        href="/findings/readiness"
        label={t("kpi.readiness")}
        value={null}
        unavailableLabel={tc("notAvailable")}
      />
    );
  } else if (readiness) {
    kpi.readiness = (
      <KpiCard
        key="readiness"
        href={`/findings/readiness?profile=${readiness.profileKey}`}
        label={t("kpi.readiness")}
        value={readinessPercent === null ? null : `${count(readinessPercent)}%`}
        unavailableLabel={tc("notAvailable")}
        comparison={t("kpi.readinessNote", {
          profile: readiness.label,
          ready: count(readiness.ready) ?? "—",
          students: count(readiness.students) ?? "—",
        })}
      />
    );
  }
  const KPI_ORDER: Record<HomeRole, readonly string[]> = {
    office: ["blockers", "warnings", "affected", "pending"],
    principal: ["readiness", "blockers", "pending", "feesNet", "feeApprovals", "warnings"],
    accountant: ["feesNet", "feesCash", "feeApprovals", "pending", "blockers"],
    teacher: ["blockers", "pending"],
  };
  const kpis = KPI_ORDER[role]
    .map((key) => kpi[key])
    .filter((card): card is NonNullable<ReactNode> => card !== undefined)
    .slice(0, 4);

  // ---- Waiting for you ----
  const work: Record<string, ReactNode> = {};
  if (data.checks) {
    work.checks = (
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
            {lastRun ? (
              <>
                {t("work.lastRunLabel")}{" "}
                <span className="num">{formatDateTime(lastRun) ?? ""}</span>
              </>
            ) : (
              t("work.neverRun")
            )}
          </p>
        ) : null}
      </WorkCard>
    );
  }
  if (data.pending) {
    work.changes = (
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
      </WorkCard>
    );
  }
  if (data.feeApprovals) {
    work.feeApprovals = (
      <WorkCard
        key="feeApprovals"
        title={t("work.feeApprovalsTitle")}
        icon="receipt"
        loading={data.feeApprovals.status === "loading"}
        action={
          <ButtonLink href="/fees/approvals" size="sm" variant="secondary">
            {t("work.feeApprovalsAction")}
            <Icon name="arrowRight" className="size-4" />
          </ButtonLink>
        }
      >
        {data.feeApprovals.status === "error" || data.feeApprovals.status === "unavailable" ? (
          <p>{t("work.loadError")}</p>
        ) : null}
        {feeApprovals !== null ? (
          <p>
            {feeApprovals === 0
              ? t("work.feeApprovalsNone")
              : t("work.feeApprovalsBody", { count: feeApprovals })}
          </p>
        ) : null}
      </WorkCard>
    );
  }
  if (data.fees) {
    work.dayClose = (
      <WorkCard
        key="dayClose"
        title={t("work.dayCloseTitle")}
        icon="calendar"
        loading={data.fees.status === "loading"}
        action={
          <ButtonLink href="/fees/daily" size="sm" variant="secondary">
            {t("work.dayCloseAction")}
            <Icon name="arrowRight" className="size-4" />
          </ButtonLink>
        }
      >
        {data.fees.status === "error" || data.fees.status === "unavailable" ? (
          <p>{t("work.loadError")}</p>
        ) : null}
        {fees ? <p>{fees.closed ? t("work.dayClosed") : t("work.dayOpen")}</p> : null}
        {fees ? (
          <p className="text-ink-subtle">
            {t("work.receiptsTotalLabel")}{" "}
            <span className="num">{formatInr(fees.receiptsTotal, locale) ?? "—"}</span>
          </p>
        ) : null}
      </WorkCard>
    );
  }
  if (data.imports) {
    work.imports = (
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
                  className="font-semibold text-primary underline-offset-4 hover:underline"
                >
                  <SourceName source={item.source} />
                </Link>
                <ImportStatusBadge status={item.status} />
                <span className="num w-full text-xs text-ink-subtle">
                  {formatDateTime(item.created_at)}
                  {" · "}
                  {t("work.rows", { count: item.row_count })}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </WorkCard>
    );
  }
  const WORK_ORDER: Record<HomeRole, readonly string[]> = {
    office: ["checks", "changes", "imports", "feeApprovals", "dayClose"],
    principal: ["changes", "feeApprovals", "checks", "dayClose", "imports"],
    accountant: ["dayClose", "feeApprovals", "changes", "checks", "imports"],
    teacher: ["changes", "checks", "imports"],
  };
  const workItems = WORK_ORDER[role]
    .map((key) => work[key])
    .filter((item): item is NonNullable<ReactNode> => item !== undefined);

  // ---- Teacher: the day's two jobs as large tap targets (report §2 teacher row) ----
  const tasks: ReactNode[] = [];
  if (role === "teacher") {
    if (can(HOME_PERM.attendance)) {
      tasks.push(
        <TaskLink
          key="attendance"
          href="/attendance"
          icon="check"
          title={t("teacher.attendanceTitle")}
          body={t("teacher.attendanceBody")}
          primary
        />,
      );
    }
    if (can(HOME_PERM.marks)) {
      tasks.push(
        <TaskLink
          key="marks"
          href="/marks"
          icon="chart"
          title={t("teacher.marksTitle")}
          body={t("teacher.marksBody")}
          primary={tasks.length === 0}
        />,
      );
    }
    if (can(HOME_PERM.ask)) {
      tasks.push(
        <TaskLink
          key="ask"
          href="/ask"
          icon="sparkles"
          title={t("askAction")}
          body={t("teacher.askBody")}
        />,
      );
    }
  }

  // ---- The one primary action per role ----
  const askLink = can(HOME_PERM.ask) ? (
    <ButtonLink key="ask" href="/ask" variant="secondary">
      <Icon name="sparkles" className="size-4" />
      {t("askAction")}
    </ButtonLink>
  ) : null;
  let primary: ReactNode = null;
  if (role === "accountant" && data.fees && can(HOME_PERM.feeCollect)) {
    primary = (
      <ButtonLink href="/fees/collect">
        <Icon name="receipt" className="size-4" />
        {t("primary.collectFees")}
      </ButtonLink>
    );
  } else if (role === "principal" && data.pending) {
    primary = (
      <ButtonLink href="/change-requests">
        <Icon name="clipboard" className="size-4" />
        {t("primary.reviewApprovals")}
      </ButtonLink>
    );
  } else if (role === "office" && can(HOME_PERM.addStudent)) {
    primary = (
      <ButtonLink href="/students/new">
        <Icon name="plus" className="size-4" />
        {t("primary.addStudent")}
      </ButtonLink>
    );
  }
  // Teachers' primary job is the first task card; the header keeps only Ask (secondary).
  const headerActions =
    role === "teacher" ? null : (
      <>
        {primary}
        {askLink}
      </>
    );

  // Getting started: shown until we know the school has imported records (office staff and
  // principals set the school up; teachers and accountants never get these steps).
  const isNew = !imports || imports.length === 0;
  const showSetup = isNew && (role === "office" || role === "principal");
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

  const nothing =
    data.permissions !== null && workItems.length === 0 && tasks.length === 0 && !showSetup;

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        eyebrow={t("eyebrow")}
        description={data.name ? t("greeting", { name: data.name }) : t("description")}
        actions={headerActions}
      />
      {tasks.length > 0 ? (
        <section aria-labelledby="home-tasks" className="space-y-3">
          <h2 id="home-tasks" className="text-base font-semibold text-ink">
            {t("teacher.title")}
          </h2>
          <p className="text-sm text-ink-muted">{t("teacher.scopeNote")}</p>
          <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{tasks}</ul>
        </section>
      ) : null}
      {kpis.length > 0 ? (
        <section aria-label={t("kpi.label")}>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{kpis}</div>
        </section>
      ) : null}
      {workItems.length > 0 ? (
        <section aria-labelledby="home-work" className="rounded-xl border border-border bg-surface">
          <h2
            id="home-work"
            className="border-b border-border px-4 py-3 text-base font-semibold text-ink sm:px-5"
          >
            {t("work.title")}
          </h2>
          <ul className="divide-y divide-border">{workItems}</ul>
        </section>
      ) : null}
      {showSetup ? (
        <Card title={t("gettingStartedTitle")} description={t("gettingStartedBody")}>
          <Timeline items={steps} label={t("steps.label")} />
        </Card>
      ) : null}
      {nothing ? (
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
  const roles = me.data?.roles ?? null;
  const role = homeRoleOf(roles);
  const has = (permission: string | readonly string[]) => {
    const wanted = typeof permission === "string" ? [permission] : permission;
    return Boolean(permissions && wanted.some((item) => permissions.includes(item)));
  };
  const canChecks = has(HOME_PERM.findings);
  const canChanges = has(HOME_PERM.changes);
  const canImports = has(HOME_PERM.imports);
  // Money and portal readiness only on the homes whose job they are (report §2).
  const canFees = has(HOME_PERM.feeRead) && (role === "accountant" || role === "principal");
  const canFeeApprovals =
    has(HOME_PERM.feeApprove) && (role === "accountant" || role === "principal");
  const canReadiness = canChecks && role === "principal";

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
  // Fees: the same queries (and keys) as the fee screens: status gives "today", then the day book.
  const feeStatus = useApiQuery(FEE_KEYS.status, () => unwrap(api.GET("/api/v1/fees/status")), {
    enabled: canFees || canFeeApprovals,
  });
  const feesOn = feeStatus.status === "ready" && feeStatus.data.enabled !== false;
  const today = feeStatus.status === "ready" ? feeStatus.data.today : "";
  const daily = useApiQuery(
    FEE_KEYS.daily(today),
    () => unwrap(api.GET("/api/v1/fees/reports/daily", { params: { query: { date: today } } })),
    { enabled: canFees && feesOn && today !== "" },
  );
  const approvals = useApiQuery(
    FEE_KEYS.approvals,
    () => unwrap(api.GET("/api/v1/fees/approvals")),
    { enabled: canFeeApprovals && feesOn },
  );
  // Readiness: the first profile with a readiness check, as on the readiness screen.
  const profiles = useApiQuery(DQ_KEYS.profiles, () => unwrap(api.GET("/api/v1/dq/profiles")), {
    enabled: canReadiness,
  });
  const firstProfile =
    profiles.status === "ready"
      ? (profiles.data.find((profile) => profile.readiness === true) ?? null)
      : null;
  const readinessSummary = useApiQuery(
    READINESS_KEYS.summary(firstProfile?.key ?? ""),
    () =>
      unwrap(
        api.GET("/api/v1/dq/readiness/{profile_key}", {
          params: { path: { profile_key: firstProfile?.key ?? "" } },
        }),
      ),
    { enabled: canReadiness && firstProfile !== null },
  );

  // Fees switched off for the school (404 module_hidden) hide the fee sections entirely.
  const feesHidden = isFeesOff(feeStatus) || (feeStatus.status === "ready" && !feesOn);
  const fees: Loadable<FeesToday> | null =
    !canFees || feesHidden
      ? null
      : feeStatus.status !== "ready"
        ? feeStatus
        : daily.status !== "ready"
          ? daily
          : {
              status: "ready",
              data: {
                date: daily.data.date,
                receiptsTotal: daily.data.receipts_total,
                netTotal: daily.data.net_total,
                cashToHandOver: daily.data.cash_to_hand_over,
                receiptCount: daily.data.receipts.length,
                closed: daily.data.closed !== null,
              },
            };
  const feeApprovals: Loadable<number> | null =
    !canFeeApprovals || feesHidden
      ? null
      : feeStatus.status !== "ready"
        ? feeStatus
        : approvals.status !== "ready"
          ? approvals
          : { status: "ready", data: approvals.data.filter((item) => item.can_decide).length };
  const readiness: Loadable<ReadinessToday | null> | null = !canReadiness
    ? null
    : profiles.status !== "ready"
      ? profiles
      : firstProfile === null
        ? null
        : readinessSummary.status !== "ready"
          ? readinessSummary
          : {
              status: "ready",
              data: {
                profileKey: firstProfile.key,
                label: firstProfile.label_en,
                ready: readinessSummary.data.totals.ready,
                students: readinessSummary.data.totals.students,
              },
            };

  return (
    <HomeView
      data={{
        name: me.data?.display_name?.trim() || null,
        permissions,
        roles,
        checks: canChecks ? checks : null,
        pending: canChanges ? pending : null,
        imports: canImports ? imports : null,
        fees,
        feeApprovals,
        readiness,
      }}
    />
  );
}
