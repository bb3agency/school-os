"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { NOTIFICATION_KEYS } from "./data";
import { NotificationItem, useMarkAllRead, useMarkRead } from "./NotificationBell";

const PAGE_SIZE = 50;

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
      <label className="inline-flex min-h-8 items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={unreadOnly}
          onChange={(event) => setUnreadOnly(event.target.checked)}
          className="size-4"
        />
        {t("unreadOnly")}
      </label>
      <ApiErrorAlert error={markRead.error ?? markAll.error} />
      <Card>
        {list.isPending ? (
          <LoadingState label={tc("loading")} />
        ) : list.isError ? (
          <ApiErrorAlert error={list.error} namespace="notifications" />
        ) : rows.length === 0 ? (
          <EmptyState title={t("empty")} body={t("emptyBody")} />
        ) : (
          <ul className="divide-y divide-border">
            {rows.map((item) => (
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
