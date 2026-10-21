"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { type FilterChip } from "@/components/ui/FilterChips";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { TabNav } from "@/components/ui/TabNav";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import { toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { DueCell, RequestStatusPill } from "./parts";
import {
  PRIVACY_KEYS,
  PRIVACY_MANAGE,
  PRIVACY_READ,
  REQUEST_KINDS,
  REQUEST_TABS,
  isOpen,
  isRequestKind,
  searchBody,
  type PrivacyRequestRow,
  type RequestKind,
  type RequestTab,
} from "./types";

/**
 * Requests and complaints (US-2601; FR-DPDP-005..007; docs/17 §5.10): open, overdue and closed
 * requests with the days left to answer. The list is a POST search, so nothing about the person
 * goes in a URL; the tab is the only thing in the address.
 */
export function RequestsScreen({ tab }: { tab: RequestTab }) {
  const t = useTranslations("privacy.requests");
  const tk = useTranslations("privacy.kinds");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const mePending = useStaffMeQuery().isPending;
  const [kind, setKind] = useState<RequestKind | "">("");
  const readable = can(PRIVACY_READ);

  const list = useInfiniteQuery({
    queryKey: PRIVACY_KEYS.list(tab, kind),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.POST("/api/v1/privacy/requests/search", {
          body: { ...searchBody(tab, kind), ...(pageParam ? { cursor: pageParam } : {}) },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: readable,
    retry: false,
  });
  const rows = list.data?.pages.flatMap((page) => page.data) ?? [];

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        can(PRIVACY_MANAGE) ? (
          <ButtonLink href="/privacy/requests/new">
            <Icon name="plus" className="size-4" />
            {t("logRequest")}
          </ButtonLink>
        ) : null
      }
    />
  );

  if (!mePending && !readable) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  const columns: ColumnV2<PrivacyRequestRow>[] = [
    {
      key: "reference",
      stack: "title",
      header: t("colReference"),
      cell: (row) => (
        <Link
          href={`/privacy/requests/${row.id}`}
          className="num font-semibold whitespace-nowrap text-primary underline-offset-4 hover:underline"
        >
          {row.reference}
        </Link>
      ),
    },
    { key: "kind", header: t("colKind"), cell: (row) => tk(`${row.kind}.label`) },
    {
      key: "about",
      header: t("colAbout"),
      cell: (row) => (
        <span className="break-words">
          <Value>{row.subject_label}</Value>
        </span>
      ),
    },
    {
      key: "received",
      header: t("colReceived"),
      cell: (row) => <span className="num whitespace-nowrap">{formatDate(row.received_on)}</span>,
    },
    {
      key: "due",
      header: t("colDue"),
      cell: (row) => (
        <DueCell
          dueOn={row.due_on}
          daysLeft={row.days_left}
          overdue={row.overdue}
          open={isOpen(row.status)}
        />
      ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <RequestStatusPill status={row.status} />,
    },
  ];

  const tabs = REQUEST_TABS.map((id) => ({
    id,
    href: id === "open" ? "/privacy/requests" : `/privacy/requests?tab=${id}`,
    label: t(`tabs.${id}`),
  }));
  const chips: FilterChip[] = kind
    ? [{ id: "kind", label: t("filterKind"), value: tk(`${kind}.label`) }]
    : [];

  return (
    <div className="space-y-5">
      {header}
      <TabNav label={t("tabsLabel")} items={tabs} activeId={tab} />
      <DataTableV2
        id="privacy-requests"
        stacked
        caption={t(`tabs.${tab}`)}
        captionHidden
        columns={columns}
        state={toLoadable({ ...list, data: rows })}
        rowKey={(row) => row.id}
        toolbar={
          <SelectField
            label={t("filterKind")}
            placeholder={t("allKinds")}
            value={kind}
            className="sm:w-64"
            onChange={(event) => {
              const value = event.currentTarget.value;
              setKind(isRequestKind(value) ? value : "");
            }}
            options={REQUEST_KINDS.map((value) => ({ value, label: tk(`${value}.label`) }))}
          />
        }
        chips={chips}
        onRemoveChip={() => setKind("")}
        onClearFilters={() => setKind("")}
        emptyTitle={t(`empty.${tab}.title`)}
        emptyBody={t(`empty.${tab}.body`)}
        onRetry={() => void list.refetch()}
        footer={
          list.hasNextPage ? (
            <div className="flex justify-center p-3">
              <Button
                variant="secondary"
                onClick={() => void list.fetchNextPage()}
                disabled={list.isFetchingNextPage}
              >
                {list.isFetchingNextPage ? tc("loading") : t("showMore")}
              </Button>
            </div>
          ) : null
        }
      />
    </div>
  );
}
