"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Timeline, type TimelineItem } from "@/components/ui/Timeline";
import { Value } from "@/components/ui/Value";
import { useSectionOptions } from "@/features/findings/data";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { toLoadable, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatDateTime, formatList } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import { GRANT_STATUSES, type Grant, type GrantStatus } from "./filters";

export const BREAKGLASS_APPROVE = "breakglass.approve";

export const BREAKGLASS_KEYS = {
  all: ["staff", "breakglass"],
  one: (id: string) => ["staff", "breakglass", "one", id] as const,
} as const;

const statusTone: Record<GrantStatus, BadgeTone> = {
  requested: "warning",
  approved: "info",
  active: "danger",
  expired: "neutral",
  revoked: "neutral",
  denied: "neutral",
};

export function GrantStatusBadge({ status }: { status: GrantStatus }) {
  const t = useTranslations("breakGlass.status");
  return <Badge tone={statusTone[status]}>{t(status)}</Badge>;
}

/** "2 hours" or "90 minutes" in the reader's language. */
function useDuration(): (minutes: number | null) => string | null {
  const t = useTranslations("breakGlass");
  return (minutes) => {
    if (minutes === null) return null;
    return minutes % 60 === 0
      ? t("durationHours", { hours: minutes / 60 })
      : t("durationMinutes", { minutes });
  };
}

/** The only scope keys that narrow the grant (they become membership scopes, app.breakglass). */
const NARROWING_KEYS = ["section_id", "class_id"] as const;

/**
 * The real reach of the grant as the school reads it: "Section: Class 9 · A", or "The whole
 * school". Audit DL-10: any other key (student, import, document...) does not narrow access, so
 * it is never shown as if it did; it is listed apart as not limiting what support can see.
 */
function ScopeText({ scope }: { scope: Grant["scope"] }) {
  const t = useTranslations("breakGlass");
  const { sections } = useSectionOptions();
  const entries = Object.entries(scope);
  const narrowing = entries.filter(([key]) => (NARROWING_KEYS as readonly string[]).includes(key));
  const ignored = entries.filter(([key]) => !(NARROWING_KEYS as readonly string[]).includes(key));
  const textOf = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value));
  return (
    <>
      <div data-testid="breakglass-reach">
        {narrowing.length === 0 ? (
          <span className="font-semibold">{t("scopeWholeSchool")}</span>
        ) : (
          <ul className="space-y-1">
            {narrowing.map(([key, value]) => {
              const text = textOf(value);
              const section =
                key === "section_id" ? sections.find((item) => item.id === text) : null;
              return (
                <li key={key}>
                  <span className="font-semibold">
                    {translateOr(t, `scopeKeys.${key}`, "scopeKeys.other")}:
                  </span>{" "}
                  <span className={section ? "" : "font-mono text-sm break-all"}>
                    {section ? section.label : text}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {ignored.length > 0 ? (
        <p className="mt-1 text-sm text-ink-muted" data-testid="breakglass-scope-ignored">
          {t("scopeIgnored", {
            items: ignored
              .map(
                ([key, value]) =>
                  `${translateOr(t, `scopeKeys.${key}`, "scopeKeys.other")} ${textOf(value)}`,
              )
              .join("; "),
          })}
        </p>
      ) : null}
    </>
  );
}

function useCanApprove(): { loading: boolean; allowed: boolean; canReadAudit: boolean } {
  const me = useStaffMeQuery();
  const permissions = me.data?.permissions ?? [];
  return {
    loading: me.isPending,
    allowed: permissions.includes(BREAKGLASS_APPROVE),
    canReadAudit: permissions.includes("audit.read"),
  };
}

/** Home › Support access (› this request). */
function useCrumbs(): (current?: string) => { label: string; href?: string }[] {
  const t = useTranslations("breakGlass");
  const tn = useTranslations("school.nav");
  return (current) =>
    current
      ? [
          { label: tn("home"), href: "/" },
          { label: t("title"), href: "/break-glass" },
          { label: current },
        ]
      : [{ label: tn("home"), href: "/" }, { label: t("title") }];
}

function NoAccess() {
  const t = useTranslations("breakGlass");
  const crumbs = useCrumbs();
  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} breadcrumb={crumbs()} />
      <Alert tone="warning" title={t("noAccessTitle")}>
        {t("noAccessBody")}
      </Alert>
    </div>
  );
}

const PAGE_SIZE = 50;

/**
 * Support access (US-103, FR-OPS-004, SEC-021; docs/07 §6.4): every request SchoolOS support
 * made to see this school's records, and the access it became, newest first. Opening the list
 * also fetches new requests from SchoolOS. Owner and principal (`breakglass.approve`) only.
 */
export function BreakGlassScreen({ status }: { status: GrantStatus | null }) {
  const t = useTranslations("breakGlass");
  const tc = useTranslations("common");
  const tstatus = useTranslations("breakGlass.status");
  const api = useBffClient("staff");
  const access = useCanApprove();
  const hours = useDuration();
  const crumbs = useCrumbs();
  const list = useInfiniteQuery({
    queryKey: [...BREAKGLASS_KEYS.all, "list", status],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/breakglass/requests", {
          params: {
            query: {
              limit: PAGE_SIZE,
              ...(status ? { status } : {}),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: access.allowed,
    retry: false,
  });

  if (access.loading) return <LoadingState label={tc("loading")} />;
  if (!access.allowed) return <NoAccess />;

  const rows = list.data?.pages.flatMap((one) => one.data) ?? [];
  const waiting = rows.filter((row) => row.status === "requested").length;
  const columns: Column<Grant>[] = [
    {
      key: "requested",
      header: t("colRequested"),
      cell: (row) => <Value>{formatDateTime(row.requested_at ?? row.created_at)}</Value>,
    },
    {
      key: "who",
      header: t("colWho"),
      cell: (row) => <Value>{row.operator_display_name}</Value>,
    },
    {
      key: "why",
      header: t("colWhy"),
      className: "min-w-56",
      cell: (row) => (
        <span>
          <span className="block font-semibold">
            {translateOr(t, `reasonCodes.${row.reason_code}`, "reasonCodes.other")}
            {row.emergency ? (
              <Badge tone="danger" className="ml-2">
                {t("emergency")}
              </Badge>
            ) : null}
          </span>
          <span className="block text-sm">{row.reason}</span>
        </span>
      ),
    },
    {
      key: "duration",
      header: t("colDuration"),
      cell: (row) => <Value>{hours(row.duration_minutes)}</Value>,
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => (
        <span className="flex flex-col items-start gap-1">
          <GrantStatusBadge status={row.status} />
          {row.status === "active" && row.expires_at ? (
            <span className="text-xs text-ink-muted">
              {t("endsAt", { time: formatDateTime(row.expires_at) ?? "" })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "open",
      header: tc("actions"),
      cell: (row) => (
        <Link
          href={`/break-glass/${row.id}`}
          className="font-semibold whitespace-nowrap text-primary underline"
        >
          {row.status === "requested" ? t("decide") : t("open")}
          <span className="sr-only">: {formatDateTime(row.requested_at ?? row.created_at)}</span>
        </Link>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} breadcrumb={crumbs()} />
      {waiting > 0 ? <Alert tone="warning" title={t("waitingCount", { count: waiting })} /> : null}
      <Alert tone="info" title={t("howTitle")}>
        {t("howBody")}
      </Alert>
      <Card title={t("filtersTitle")} padding="sm">
        <form method="get" className="flex flex-wrap items-end gap-3">
          <SelectField
            name="status"
            label={t("filterStatus")}
            defaultValue={status ?? ""}
            options={[
              { value: "", label: tc("all") },
              ...GRANT_STATUSES.map((value) => ({ value, label: tstatus(value) })),
            ]}
          />
          <Button type="submit" variant="secondary">
            {tc("applyFilters")}
          </Button>
          {access.canReadAudit ? (
            <Link
              href="/audit?action=breakglass.access"
              className="self-center text-sm text-primary underline"
            >
              {t("auditLink")}
            </Link>
          ) : null}
        </form>
      </Card>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...list, data: rows })}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
      {list.hasNextPage ? (
        <div className="flex justify-center">
          <Button
            variant="secondary"
            onClick={() => void list.fetchNextPage()}
            disabled={list.isFetchingNextPage}
          >
            {list.isFetchingNextPage ? tc("loading") : t("showMore")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

const noInput = z.object({});

/**
 * The life of one request as a timeline: asked → decided → access started → access ends (or
 * ended by the school). Only the steps with a time from the API are shown; while access is
 * open, "Access started" is the current step and its end is still to come.
 */
function GrantTimeline({ grant }: { grant: Grant }) {
  const t = useTranslations("breakGlass");
  const td = useTranslations("breakGlass.detail");
  const ts = useTranslations("breakGlass.timeline");
  const open = grant.status === "active" || grant.status === "approved";
  const at = (value: string | null | undefined) => <Value>{formatDateTime(value ?? null)}</Value>;
  const items: TimelineItem[] = [
    {
      id: "requested",
      title: t("colRequested"),
      time: at(grant.requested_at ?? grant.created_at),
      status: "done",
      statusLabel: ts("done"),
    },
  ];
  if (grant.decided_at) {
    items.push({
      id: "decided",
      title: td("decidedAt"),
      time: at(grant.decided_at),
      chips: <GrantStatusBadge status={grant.status} />,
      status: "done",
      statusLabel: ts("done"),
    });
  }
  if (grant.starts_at) {
    items.push({
      id: "started",
      title: td("startsAt"),
      time: at(grant.starts_at),
      status: open ? "current" : "done",
      statusLabel: open ? ts("now") : ts("done"),
    });
  }
  if (grant.revoked_at) {
    items.push({
      id: "revoked",
      title: td("revokedAt"),
      time: at(grant.revoked_at),
      status: "done",
      statusLabel: ts("done"),
    });
  } else if (grant.expires_at) {
    items.push({
      id: "expires",
      title: td("expiresAt"),
      time: at(grant.expires_at),
      status: open ? "pending" : "done",
      statusLabel: open ? ts("later") : ts("done"),
    });
  }
  return <Timeline items={items} label={td("timelineTitle")} />;
}

/**
 * One support-access request or grant (US-103 AC1/AC2): reason, scope, duration and who asked;
 * approve or deny while it waits, end it early while it is open. Every decision needs step-up
 * MFA; the API refuses anyone but the owner and principal and never lets the approver be the
 * person who gets access.
 */
export function BreakGlassDetailScreen({ grantId }: { grantId: string }) {
  const t = useTranslations("breakGlass");
  const td = useTranslations("breakGlass.detail");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const access = useCanApprove();
  const hours = useDuration();
  const crumbs = useCrumbs();
  const grant = useApiQuery(
    BREAKGLASS_KEYS.one(grantId),
    () =>
      unwrap(
        api.GET("/api/v1/breakglass/requests/{request_id}", {
          params: { path: { request_id: grantId } },
        }),
      ),
    { enabled: access.allowed },
  );

  if (access.loading) return <LoadingState label={tc("loading")} />;
  if (!access.allowed) return <NoAccess />;
  if (grant.status === "loading") return <LoadingState label={tc("loading")} />;
  if (grant.status !== "ready") {
    return (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {grant.status === "error" && grant.reason
          ? te(`load.${grant.reason}`)
          : tc("loadErrorBody")}
      </Alert>
    );
  }
  const data = grant.data;
  const invalidate = [BREAKGLASS_KEYS.all] as const;
  const path = { request_id: data.id };
  const duration = hours(data.duration_minutes);
  const canDecide = data.status === "requested" && !data.emergency;
  const canRevoke = data.status === "active" || data.status === "approved";

  return (
    <div className="space-y-6">
      <PageHeader
        title={td("title")}
        breadcrumb={crumbs(td("title"))}
        description={translateOr(t, `reasonCodes.${data.reason_code}`, "reasonCodes.other")}
        badge={
          <span className="flex flex-wrap gap-2">
            <GrantStatusBadge status={data.status} />
            {data.emergency ? <Badge tone="danger">{t("emergency")}</Badge> : null}
          </span>
        }
        actions={
          <>
            {canDecide ? (
              <>
                <ActionDialog
                  triggerLabel={td("approve")}
                  triggerVariant="primary"
                  title={td("approveTitle")}
                  description={td("approveBody", { duration: duration ?? "" })}
                  confirmLabel={td("approve")}
                  stepUp
                  schema={noInput}
                  invalidate={invalidate}
                  errorNamespace="breakGlass"
                  submit={() =>
                    unwrap(
                      api.POST("/api/v1/breakglass/requests/{request_id}/approve", {
                        params: { path },
                      }),
                    )
                  }
                />
                <ActionDialog
                  triggerLabel={td("deny")}
                  triggerVariant="secondary"
                  title={td("denyTitle")}
                  description={td("denyBody")}
                  confirmLabel={td("deny")}
                  confirmVariant="danger"
                  stepUp
                  schema={noInput}
                  invalidate={invalidate}
                  errorNamespace="breakGlass"
                  submit={() =>
                    unwrap(
                      api.POST("/api/v1/breakglass/requests/{request_id}/deny", {
                        params: { path },
                      }),
                    )
                  }
                />
              </>
            ) : null}
            {canRevoke ? (
              <ActionDialog
                triggerLabel={td("revoke")}
                triggerVariant="danger"
                title={td("revokeTitle")}
                description={td("revokeBody")}
                confirmLabel={td("revoke")}
                confirmVariant="danger"
                stepUp
                schema={noInput}
                invalidate={invalidate}
                errorNamespace="breakGlass"
                submit={() =>
                  unwrap(
                    api.POST("/api/v1/breakglass/grants/{grant_id}/revoke", {
                      params: { path: { grant_id: data.id } },
                    }),
                  )
                }
              />
            ) : null}
          </>
        }
      />

      {data.status === "active" ? (
        <Alert tone="warning" title={td("activeTitle")}>
          {td("activeBody", { time: formatDateTime(data.expires_at) ?? "" })}
        </Alert>
      ) : null}
      {data.emergency && data.status !== "denied" ? (
        <Alert tone="danger" title={td("emergencyTitle")}>
          {td("emergencyBody")}
        </Alert>
      ) : null}

      <Card title={td("requestTitle")}>
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-[max-content_1fr]">
          <dt className="font-semibold">{t("colWho")}</dt>
          <dd>
            <Value>{data.operator_display_name}</Value>
            <span className="block text-sm text-ink-muted">{td("whoNote")}</span>
          </dd>
          <dt className="font-semibold">{t("colWhy")}</dt>
          <dd className="whitespace-pre-line">{data.reason}</dd>
          <dt className="font-semibold">{td("scope")}</dt>
          <dd>
            <ScopeText scope={data.scope} />
          </dd>
          <dt className="font-semibold">{t("colDuration")}</dt>
          <dd>
            <Value>{duration}</Value>
          </dd>
          <dt className="font-semibold">{td("canSee")}</dt>
          <dd>
            {formatList([td("seeStudents"), td("seeFindings"), td("seeDocuments")], locale)}
            <span className="block text-sm text-ink-muted">{td("readOnly")}</span>
          </dd>
        </dl>
      </Card>

      <Card title={td("timelineTitle")}>
        <GrantTimeline grant={data} />
      </Card>

      <p className="flex flex-wrap gap-x-4">
        {access.canReadAudit ? (
          <Link href="/audit?action=breakglass.access" className="text-primary underline">
            {t("auditLink")}
          </Link>
        ) : null}
        <Link href="/break-glass" className="text-primary underline">
          {td("backToList")}
        </Link>
      </p>
    </div>
  );
}
