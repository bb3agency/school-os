import type { components } from "@schoolos/api-client";

/** Notification helpers (FR-NOT-001). Plain module: no React, safe for tests. */

export type Notification = components["schemas"]["NotificationOut"];

export const NOTIFICATION_KEYS = {
  all: ["staff", "notifications"],
  unread: ["staff", "notifications", "unread-count"],
  latest: ["staff", "notifications", "latest"],
  list: (unreadOnly: boolean) => ["staff", "notifications", "list", unreadOnly] as const,
} as const;

/** Normal polling interval of the bell (the badge is not urgent). */
export const BELL_POLL_MS = 60_000;
/** Longest wait between polls while the server keeps failing. */
export const BELL_MAX_POLL_MS = 15 * 60_000;

/**
 * Wait before the next unread-count poll: every minute, doubling after each failure in a row
 * (1, 2, 4, 8 … minutes, at most 15) so a struggling server or a patchy office connection is
 * not hammered. Polling stops while the tab is hidden (TanStack Query pauses intervals in the
 * background and refetches when the tab is visible again) and after 401/403 (signed out or
 * the school is paused).
 */
export function bellPollDelay(consecutiveFailures: number): number {
  const factor = 2 ** Math.min(Math.max(consecutiveFailures, 0), 10);
  return Math.min(BELL_POLL_MS * factor, BELL_MAX_POLL_MS);
}

/** Screens that notifications may open. Other resource types are shown without a link. */
const LINKS: Record<string, (id: string) => string> = {
  change_request: (id) => `/change-requests/${id}`,
  dq_run: (id) => `/findings/runs/${id}`,
  breakglass_grant: (id) => `/break-glass/${id}`,
  export: (id) => `/exports/${id}`,
  // FR-ADM-001: the full data export has one screen (status, download, history).
  tenant_export: () => "/settings/data-export",
  import_batch: (id) => `/imports/${id}`,
  extraction_batch: (id) => `/register-photos/${id}`,
  // document.quarantined: the document screen explains why the file was blocked.
  document: (id) => `/documents/${id}`,
  // M4: a circular's reading is ready or needs manual review; a task was given or is due.
  circular: (id) => `/circulars/${id}`,
  task: () => "/tasks",
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function notificationHref(item: Pick<Notification, "resource_type" | "resource_id">) {
  if (!item.resource_type || !item.resource_id || !UUID.test(item.resource_id)) return null;
  const link = LINKS[item.resource_type];
  return link ? link(item.resource_id) : null;
}
