"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Eyebrow } from "@/components/ui/Eyebrow";
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
    <div className="space-y-6">
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
      <ApiErrorAlert error={markRead.error ?? markAll.error} />
      <Card>
        {list.isPending ? (
          <LoadingState label={tc("loading")} />
        ) : list.isError ? (
          <ApiErrorAlert error={list.error} namespace="notifications" />
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
                  <Eyebrow as="h2" id={headingId} className="mb-1">
                    {group.day === today ? t("today", { date: group.day }) : group.day}
                  </Eyebrow>
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
      </Card>
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
