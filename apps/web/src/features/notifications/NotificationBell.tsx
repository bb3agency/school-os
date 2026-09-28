"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Link } from "@/i18n/navigation";
import { PASSIVE_HEADER } from "@/lib/bff/fetch";
import { ApiError, unwrap, useApiMutation, useBffClient } from "@/lib/bff/query";
import { formatDateTime } from "@/lib/format";
import { bellPollDelay, NOTIFICATION_KEYS, notificationHref, type Notification } from "./data";

const LATEST = 8;

/** One notification line: title, body, time, unread marker, and a link when it has one. */
export function NotificationItem({
  item,
  onOpen,
}: {
  item: Notification;
  onOpen: (item: Notification) => void;
}) {
  const t = useTranslations("notifications");
  const href = notificationHref(item);
  const unread = item.read_at === null;
  const text = (
    <>
      <span className="block font-semibold">
        {unread ? <span className="sr-only">{t("unread")}: </span> : null}
        {item.title}
      </span>
      <span className="block text-sm">{item.body}</span>
      <span className="block text-xs text-ink-muted">{formatDateTime(item.created_at)}</span>
    </>
  );
  return (
    <div className="flex items-start gap-2" lang={item.language}>
      <span
        aria-hidden="true"
        className={`mt-2 size-2 shrink-0 rounded-full ${unread ? "bg-primary" : "bg-transparent"}`}
      />
      {href ? (
        <Link
          href={href}
          onClick={() => onOpen(item)}
          className="block flex-1 rounded-md p-1 hover:bg-surface-muted"
        >
          {text}
        </Link>
      ) : (
        <div className="flex-1 p-1">
          {text}
          {unread ? (
            <Button size="sm" variant="ghost" onClick={() => onOpen(item)} className="mt-1 px-0">
              {t("markRead")}
              <span className="sr-only">: {item.title}</span>
            </Button>
          ) : null}
        </div>
      )}
    </div>
  );
}

/** Mark one notification read (opening it) and refresh the badge and lists. */
export function useMarkRead() {
  const api = useBffClient("staff");
  return useApiMutation(
    (id: string) =>
      unwrap(
        api.POST("/api/v1/notifications/{notification_id}/read", {
          params: { path: { notification_id: id } },
        }),
      ),
    { invalidate: [NOTIFICATION_KEYS.all] },
  );
}

export function useMarkAllRead() {
  const api = useBffClient("staff");
  return useApiMutation(() => unwrap(api.POST("/api/v1/notifications/read-all")), {
    invalidate: [NOTIFICATION_KEYS.all],
  });
}

/**
 * The bell in the header (FR-NOT-001): unread count, polled in the background without
 * counting as activity (the idle lock still works on an unattended PC), every minute with
 * exponential backoff on failures, paused while the tab is hidden. Opens a panel with the
 * latest notifications (disclosure pattern: button + region; Escape or a click outside
 * closes it and focus returns to the bell).
 */
export function NotificationBell() {
  const t = useTranslations("notifications");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const panelId = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const failures = useRef(0);
  const [open, setOpen] = useState(false);

  const unread = useQuery({
    queryKey: NOTIFICATION_KEYS.unread,
    queryFn: async () => {
      try {
        const result = await unwrap(
          api.GET("/api/v1/notifications/unread-count", {
            headers: { [PASSIVE_HEADER]: "1" },
          }),
        );
        failures.current = 0;
        return result.count;
      } catch (error) {
        failures.current += 1;
        throw error;
      }
    },
    retry: false,
    refetchOnWindowFocus: true,
    refetchIntervalInBackground: false,
    refetchInterval: (query) => {
      const error = query.state.error;
      // Signed out or no access (e.g. the school is paused): stop polling.
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        return false;
      }
      return bellPollDelay(failures.current);
    },
  });
  const latest = useQuery({
    queryKey: NOTIFICATION_KEYS.latest,
    queryFn: async () =>
      (await unwrap(api.GET("/api/v1/notifications", { params: { query: { limit: LATEST } } })))
        .data,
    enabled: open,
    retry: false,
  });
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) buttonRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(true);
    };
    const onPointer = (event: PointerEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) close(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open, close]);

  const count = unread.data ?? 0;
  const label =
    unread.data === undefined ? t("bellLabel") : t("bellLabelCount", { count: unread.data });

  function openItem(item: Notification) {
    if (item.read_at === null) markRead.mutate(item.id);
    if (notificationHref(item)) setOpen(false);
  }

  return (
    <div ref={wrapperRef} className="relative" data-print="hide">
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={label}
        onClick={() => {
          if (!open) void queryClient.invalidateQueries({ queryKey: NOTIFICATION_KEYS.latest });
          setOpen((value) => !value);
        }}
        className="relative inline-flex size-10 items-center justify-center rounded-full border border-border-soft bg-surface text-ink hover:bg-surface-muted"
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="size-5"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {count > 0 ? (
          <span
            aria-hidden="true"
            className="absolute -top-1.5 -right-1.5 min-w-5 rounded-full bg-danger px-1 text-center text-xs leading-5 font-bold text-white"
          >
            {count > 99 ? "99+" : count}
          </span>
        ) : null}
      </button>
      {/* Screen readers hear new notifications without opening the panel. */}
      <span className="sr-only" role="status" aria-live="polite">
        {unread.data ? t("unreadCount", { count: unread.data }) : ""}
      </span>
      {open ? (
        <section
          id={panelId}
          aria-label={t("panelLabel")}
          className="absolute right-0 z-20 mt-2 w-[min(24rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-4 text-ink shadow-popover"
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-base font-semibold">{t("title")}</h2>
            {count > 0 ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => markAll.mutate(undefined)}
                disabled={markAll.isPending}
              >
                {t("markAllRead")}
              </Button>
            ) : null}
          </div>
          {latest.isPending ? (
            <p className="text-sm">{t("loading")}</p>
          ) : latest.isError ? (
            <ApiErrorAlert error={latest.error} namespace="notifications" />
          ) : latest.data.length === 0 ? (
            <p className="text-sm text-ink-muted">{t("empty")}</p>
          ) : (
            <ul className="max-h-[60vh] divide-y divide-border overflow-y-auto">
              {latest.data.map((item) => (
                <li key={item.id} className="py-2">
                  <NotificationItem item={item} onOpen={openItem} />
                </li>
              ))}
            </ul>
          )}
          <ApiErrorAlert error={markRead.error ?? markAll.error} />
          <p className="mt-2 border-t border-border pt-2 text-sm">
            <Link
              href="/notifications"
              onClick={() => setOpen(false)}
              className="font-semibold text-primary underline"
            >
              {t("seeAll")}
            </Link>
          </p>
        </section>
      ) : null}
    </div>
  );
}
