"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import { toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import type { ChangeRequestFilters } from "./filters";
import { ChangeRequestStatusBadge, ExpiryText, fieldLabel, ValueChange } from "./parts";
import {
  CHANGE_REQUEST_STATUSES,
  CR_APPROVE,
  CR_REQUEST,
  type ChangeRequest,
} from "./types";

export const CR_KEYS = {
  all: ["staff", "change-requests"],
  one: (id: string) => ["staff", "change-requests", "one", id] as const,
} as const;

const PAGE_SIZE = 50;

/**
 * Change requests (US-601, FR-CR-001..004): the maker-checker queue, newest first. Approvers
 * see which requests wait for them; nobody is ever offered their own request to approve.
 */
export function ChangeRequestsScreen({ filters }: { filters: ChangeRequestFilters }) {
  const t = useTranslations("changeRequests");
  const tc = useTranslations("common");
  const tstatus = useTranslations("changeRequests.status");
  const locale = useLocale();
  const api = useBffClient("staff");
  const me = useStaffMe();
  const can = useStaffCan();
  const query = {
    ...(filters.status ? { status: filters.status } : {}),
    ...(filters.studentId ? { student_id: filters.studentId } : {}),
  };
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

  const columns: Column<ChangeRequest>[] = [
    {
      key: "requested",
      header: t("colRequested"),
      cell: (row) => <Value>{formatDateTime(row.requested_at)}</Value>,
    },
    {
      key: "field",
      header: t("colField"),
      cell: (row) => fieldLabel(row, locale),
    },
    { key: "change", header: t("colChange"), cell: (row) => <ValueChange request={row} /> },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => (
        <span className="flex flex-col items-start gap-1">
          <ChangeRequestStatusBadge status={row.status} />
          {row.status === "pending" && row.can_decide && !mine(row) ? (
            <Badge tone="info">{t("waitingForYou")}</Badge>
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
      header: tc("actions"),
      cell: (row) => (
        <Link
          href={`/change-requests/${row.id}`}
          className="font-semibold whitespace-nowrap text-primary underline"
        >
          {t("open")}
          <span className="sr-only">
            : {fieldLabel(row, locale)}, {formatDateTime(row.requested_at)}
          </span>
        </Link>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          can(CR_REQUEST) ? (
            <ButtonLink
              href={
                filters.studentId
                  ? `/change-requests/new?student_id=${filters.studentId}`
                  : "/change-requests/new"
              }
            >
              {t("newRequest")}
            </ButtonLink>
          ) : null
        }
      />
      {can(CR_APPROVE) ? <p className="text-sm text-ink-muted">{t("approverHint")}</p> : null}
      <Card title={t("filtersTitle")}>
        <form method="get" className="flex flex-wrap items-end gap-4">
          {filters.studentId ? (
            <>
              <input type="hidden" name="student_id" value={filters.studentId} />
              <p className="self-center text-sm">
                {t("oneStudentOnly")}{" "}
                <Link href="/change-requests" className="text-primary underline">
                  {t("allStudents")}
                </Link>
              </p>
            </>
          ) : null}
          <SelectField
            name="status"
            label={t("filterStatus")}
            defaultValue={filters.status ?? ""}
            options={[
              { value: "", label: tc("all") },
              ...CHANGE_REQUEST_STATUSES.map((status) => ({
                value: status,
                label: tstatus(status),
              })),
            ]}
          />
          <Button type="submit" variant="secondary">
            {tc("applyFilters")}
          </Button>
        </form>
      </Card>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...list, data: rows })}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={can(CR_REQUEST) ? t("emptyBodyRequester") : t("emptyBody")}
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
