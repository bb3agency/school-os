"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { Icon } from "@/components/ui/Icon";
import { TabNav } from "@/components/ui/TabNav";
import { Value } from "@/components/ui/Value";
import { Link, useRouter } from "@/i18n/navigation";
import { toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import { changeRequestsHref, changeRequestsQuery, type ChangeRequestFilters } from "./filters";
import { ChangeRequestStatusBadge, ExpiryText, fieldLabel, ValueChange } from "./parts";
import { CHANGE_REQUEST_STATUSES, CR_APPROVE, CR_REQUEST, type ChangeRequest } from "./types";

export const CR_KEYS = {
  all: ["staff", "change-requests"],
  one: (id: string) => ["staff", "change-requests", "one", id] as const,
} as const;

const PAGE_SIZE = 50;

/**
 * Change requests (US-601, FR-CR-001..004): the maker-checker queue, newest first. Approvers
 * see which requests wait for them; nobody is ever offered their own request to approve.
 * The tabs are links (the tab is in the URL): "Waiting for me" (`waiting_for=me`, approvers
 * only), "Asked by me" (`requested_by=me`, makers only) and the status filter. Choosing an
 * inbox tab clears the status and the other way round. No counts: the list is cursor-paged
 * and carries no totals.
 */
export function ChangeRequestsScreen({ filters }: { filters: ChangeRequestFilters }) {
  const t = useTranslations("changeRequests");
  const tc = useTranslations("common");
  const tstatus = useTranslations("changeRequests.status");
  const locale = useLocale();
  const api = useBffClient("staff");
  const me = useStaffMe();
  const can = useStaffCan();
  const router = useRouter();
  const query = changeRequestsQuery(filters);
  const list = useInfiniteQuery({
    queryKey: [...CR_KEYS.all, "list", query],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/change-requests", {
          params: {
            query: { ...query, limit: PAGE_SIZE, ...(pageParam ? { cursor: pageParam } : {}) },
          },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    retry: false,
  });
  const rows = list.data?.pages.flatMap((one) => one.data) ?? [];
  const mine = (row: ChangeRequest) => me?.membership_id === row.requested_by;

  const columns: ColumnV2<ChangeRequest>[] = [
    {
      key: "requested",
      stack: "field",
      header: t("colRequested"),
      cell: (row) => (
        <span className="num text-sm whitespace-nowrap text-ink-muted">
          <Value>{formatDateTime(row.requested_at)}</Value>
        </span>
      ),
    },
    {
      key: "field",
      stack: "title",
      hideable: false,
      header: t("colField"),
      cell: (row) => <span className="font-semibold">{fieldLabel(row, locale)}</span>,
    },
    { key: "change", header: t("colChange"), cell: (row) => <ValueChange request={row} /> },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => (
        <span className="flex flex-col items-start gap-1">
          <ChangeRequestStatusBadge status={row.status} />
          {row.status === "pending" && row.can_decide && !mine(row) ? (
            <Pill variant="dark">{t("waitingForYou")}</Pill>
          ) : null}
          <span className="text-xs text-ink-muted">
            <ExpiryText request={row} />
          </span>
        </span>
      ),
    },
    {
      key: "by",
      header: t("colBy"),
      cell: (row) => (mine(row) ? t("you") : t("someoneElse")),
    },
    {
      key: "open",
      stack: "actions",
      hideable: false,
      header: tc("actions"),
      cell: (row) => (
        <Link
          href={`/change-requests/${row.id}`}
          className="font-semibold whitespace-nowrap text-primary underline-offset-4 hover:underline"
        >
          {t("open")}
          <span className="sr-only">
            : {fieldLabel(row, locale)}, {formatDateTime(row.requested_at)}
          </span>
        </Link>
      ),
    },
  ];

  const tabs = [
    {
      id: "all",
      href: changeRequestsHref({ ...filters, status: null, inbox: null }),
      label: tc("all"),
    },
    ...(can(CR_APPROVE)
      ? [
          {
            id: "waiting",
            href: changeRequestsHref({ ...filters, status: null, inbox: "waiting" }),
            label: t("tabWaitingForMe"),
          },
        ]
      : []),
    ...(can(CR_REQUEST)
      ? [
          {
            id: "mine",
            href: changeRequestsHref({ ...filters, status: null, inbox: "mine" }),
            label: t("tabMine"),
          },
        ]
      : []),
    ...CHANGE_REQUEST_STATUSES.map((status) => ({
      id: status,
      href: changeRequestsHref({ ...filters, status, inbox: null }),
      label: tstatus(status),
    })),
  ];
  const emptyTitle =
    filters.inbox === "waiting"
      ? t("emptyWaitingTitle")
      : filters.inbox === "mine"
        ? t("emptyMineTitle")
        : t("emptyTitle");
  const emptyBody =
    filters.inbox === "waiting"
      ? t("emptyWaitingBody")
      : can(CR_REQUEST)
        ? t("emptyBodyRequester")
        : t("emptyBody");
  const chips = filters.studentId
    ? [{ id: "student_id", label: t("chipStudent"), value: t("chipOneStudent") }]
    : [];

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: t("crumbHome"), href: "/" }, { label: t("title") }]}
        actions={
          can(CR_REQUEST) ? (
            <ButtonLink
              href={
                filters.studentId
                  ? `/change-requests/new?student_id=${filters.studentId}`
                  : "/change-requests/new"
              }
            >
              <Icon name="plus" className="size-4" />
              {t("newRequest")}
            </ButtonLink>
          ) : null
        }
      />
      {can(CR_APPROVE) ? <Alert tone="info">{t("approverHint")}</Alert> : null}
      <TabNav
        label={t("tabsLabel")}
        items={tabs}
        activeId={filters.inbox ?? filters.status ?? "all"}
      />
      <DataTableV2
        id="change-requests"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...list, data: rows })}
        rowKey={(row) => row.id}
        chips={chips}
        onRemoveChip={() => router.push(changeRequestsHref({ ...filters, studentId: null }))}
        onRetry={() => void list.refetch()}
        emptyTitle={emptyTitle}
        emptyBody={emptyBody}
        noResultsTitle={emptyTitle}
        footer={
          list.hasNextPage ? (
            <div className="flex justify-center">
              <Button
                variant="secondary"
                onClick={() => void list.fetchNextPage()}
                disabled={list.isFetchingNextPage}
              >
                {list.isFetchingNextPage ? tc("loading") : t("showMore")}
              </Button>
            </div>
          ) : undefined
        }
      />
    </div>
  );
}
