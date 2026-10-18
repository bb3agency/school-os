"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formatDate } from "@/lib/format";
import { NOTIFICATION_KEYS, type Notification } from "./data";
import { NotificationItem, useMarkAllRead, useMarkRead } from "./NotificationBell";

const PAGE_SIZE = 50;

export interface DayGroup {
  /** The day as shown (school date format, IST). */
  day: string;
  items: Notification[];
}

/** Newest-first notifications grouped by their day (IST), keeping the API's order. */
export function groupByDay(rows: readonly Notification[]): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const item of rows) {
    const day = formatDate(item.created_at) ?? "";
    const last = groups.at(-1);
    if (last && last.day === day) last.items.push(item);
    else groups.push({ day, items: [item] });
  }
  return groups;
}

/** All of your notifications, newest first, in your language (FR-NOT-001). */
export function NotificationsScreen() {
  const t = useTranslations("notifications");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const list = useInfiniteQuery({
    queryKey: NOTIFICATION_KEYS.list(unreadOnly),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/notifications", {
          params: {
            query: {
              limit: PAGE_SIZE,
              unread: unreadOnly,
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    retry: false,
  });
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();
  const rows = list.data?.pages.flatMap((one) => one.data) ?? [];
  const today = formatDate(new Date().toISOString());

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button
            variant="secondary"
            onClick={() => markAll.mutate(undefined)}
            disabled={markAll.isPending}
          >
            {t("markAllRead")}
          </Button>
        }
      />
      <ApiErrorAlert error={markRead.error ?? markAll.error} />
      <div className="min-w-0 rounded-xl border border-border bg-surface">
        {/* Read/unread filter: the API filters (`unread=true`), so paging stays right. */}
        <div
          className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5 sm:px-4"
          data-print="hide"
        >
          <SegmentedControl
            legend={t("filterLegend")}
            options={[
              { value: "all", label: t("filterAll") },
              { value: "unread", label: t("filterUnread") },
            ]}
            value={unreadOnly ? "unread" : "all"}
            onValueChange={(value) => setUnreadOnly(value === "unread")}
            size="sm"
          />
        </div>
        <div className="p-4 sm:p-5">
          {list.isPending ? (
            <LoadingState label={tc("loading")} />
          ) : list.isError ? (
            <div className="space-y-3">
              <ApiErrorAlert error={list.error} namespace="notifications" />
              <Button variant="secondary" size="sm" onClick={() => void list.refetch()}>
                <Icon name="refresh" className="size-4" />
                {tc("retry")}
              </Button>
            </div>
          ) : rows.length === 0 ? (
            <EmptyState
              title={unreadOnly ? t("emptyUnread") : t("empty")}
              body={unreadOnly ? t("emptyUnreadBody") : t("emptyBody")}
              icon="bell"
            />
          ) : (
            <div className="space-y-6">
              {groupByDay(rows).map((group, index) => {
                const headingId = `notifications-day-${index}`;
                return (
                  <section key={group.day || index} aria-labelledby={headingId}>
                    <h2 id={headingId} className="num mb-1 text-xs font-semibold text-ink-muted">
                      {group.day === today ? t("today", { date: group.day }) : group.day}
                    </h2>
                    <ul className="divide-y divide-border">
                      {group.items.map((item) => (
                        <li key={item.id} className="py-3">
                          <NotificationItem
                            item={item}
                            onOpen={(one) => {
                              if (one.read_at === null) markRead.mutate(one.id);
                            }}
                          />
                        </li>
                      ))}
                    </ul>
                  </section>
                );
              })}
            </div>
          )}
        </div>
        {list.hasNextPage ? (
          <div className="flex justify-center border-t border-border px-3 py-2.5 sm:px-4">
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
    </div>
  );
}
