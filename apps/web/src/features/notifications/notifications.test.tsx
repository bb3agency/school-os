import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ACTIVITY_EVENT } from "@/lib/bff/session-client";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { notification } from "@/test/school-fixtures";
import { bellPollDelay, notificationHref } from "./data";
import { NotificationBell } from "./NotificationBell";
import { NotificationsScreen } from "./NotificationsScreen";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  };
});

let stub: BffStub;
beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("bell polling (FR-NOT-001)", () => {
  it("polls every minute and backs off exponentially after failures, up to 15 minutes", () => {
    expect(bellPollDelay(0)).toBe(60_000);
    expect(bellPollDelay(1)).toBe(120_000);
    expect(bellPollDelay(3)).toBe(480_000);
    expect(bellPollDelay(4)).toBe(900_000);
    expect(bellPollDelay(50)).toBe(900_000);
  });

  it("links only to screens that exist, and only for well-formed IDs", () => {
    const id = "0192f3a4-0000-7000-8000-00000000c001";
    expect(notificationHref({ resource_type: "change_request", resource_id: id })).toBe(
      `/change-requests/${id}`,
    );
    expect(notificationHref({ resource_type: "dq_run", resource_id: id })).toBe(
      `/findings/runs/${id}`,
    );
    expect(notificationHref({ resource_type: "breakglass_grant", resource_id: id })).toBe(
      `/break-glass/${id}`,
    );
    // export.ready / export.failed, import.validated / import.committed and
    // extraction.batch.ready / .failed (app/notifications/templates.yaml resource_type).
    expect(notificationHref({ resource_type: "export", resource_id: id })).toBe(`/exports/${id}`);
    expect(notificationHref({ resource_type: "import_batch", resource_id: id })).toBe(
      `/imports/${id}`,
    );
    expect(notificationHref({ resource_type: "extraction_batch", resource_id: id })).toBe(
      `/register-photos/${id}`,
    );
    // document.quarantined opens the document screen (FR-DOC-002).
    expect(notificationHref({ resource_type: "document", resource_id: id })).toBe(
      `/documents/${id}`,
    );
    expect(notificationHref({ resource_type: "document", resource_id: "../x" })).toBeNull();
    // announcement.new stays unlinked (owner decision: the banner shows it).
    expect(notificationHref({ resource_type: "announcement", resource_id: id })).toBeNull();
    expect(notificationHref({ resource_type: "change_request", resource_id: "../x" })).toBeNull();
    expect(notificationHref({ resource_type: "export", resource_id: "../x" })).toBeNull();
  });
});

describe("notification bell (FR-NOT-001)", () => {
  it("shows the unread count from a passive poll that does not count as activity", async () => {
    stub.routes["GET /bff/api/v1/notifications/unread-count"] = () => Response.json({ count: 3 });
    const activity = vi.fn();
    window.addEventListener(ACTIVITY_EVENT, activity);
    renderWithIntl(<NotificationBell />);
    expect(
      await screen.findByRole("button", { name: "Notifications, 3 unread" }),
    ).toBeInTheDocument();
    window.removeEventListener(ACTIVITY_EVENT, activity);
    const call = stub.callsTo("GET /bff/api/v1/notifications/unread-count")[0];
    expect(call?.headers.get("x-sos-passive")).toBe("1");
    expect(activity).not.toHaveBeenCalled();
  });

  it("stays quiet (no navigation) when the session has ended", async () => {
    stub.routes["GET /bff/api/v1/notifications/unread-count"] = () =>
      problem(401, "unauthenticated");
    renderWithIntl(<NotificationBell />);
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/notifications/unread-count")).toHaveLength(1),
    );
    expect(stub.navigate).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Notifications" })).toBeInTheDocument();
  });

  it("opens the latest notifications, marks one read when opened, and closes with Escape", async () => {
    stub.routes["GET /bff/api/v1/notifications/unread-count"] = () => Response.json({ count: 1 });
    stub.routes["GET /bff/api/v1/notifications"] = () => page([notification()]);
    stub.routes["POST /bff/api/v1/notifications/0192f3a4-0000-7000-8000-00000000e0a1/read"] = () =>
      Response.json(notification({ read_at: "2026-09-26T06:00:00Z" }));
    renderWithIntl(<NotificationBell />);
    const bell = await screen.findByRole("button", { name: "Notifications, 1 unread" });
    expect(bell).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(bell);
    expect(bell).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByRole("region", { name: "Latest notifications" });
    const link = await within(panel).findByRole("link", {
      name: /A correction request is waiting for you/,
    });
    expect(link).toHaveAttribute(
      "href",
      "/en/change-requests/0192f3a4-0000-7000-8000-00000000c001",
    );
    // jsdom cannot navigate: stop the link's default action after React has handled the click.
    document.addEventListener("click", (event) => event.preventDefault(), { once: true });
    await userEvent.click(link);
    await waitFor(() =>
      expect(
        stub.callsTo("POST /bff/api/v1/notifications/0192f3a4-0000-7000-8000-00000000e0a1/read"),
      ).toHaveLength(1),
    );
    await userEvent.click(bell);
    await screen.findByRole("region", { name: "Latest notifications" });
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "Latest notifications" })).toBeNull();
    expect(bell).toHaveFocus();
  });

  it("marks everything read", async () => {
    stub.routes["GET /bff/api/v1/notifications/unread-count"] = () => Response.json({ count: 2 });
    stub.routes["GET /bff/api/v1/notifications"] = () => page([notification()]);
    stub.routes["POST /bff/api/v1/notifications/read-all"] = () => Response.json({ updated: 2 });
    renderWithIntl(<NotificationBell />);
    await userEvent.click(await screen.findByRole("button", { name: "Notifications, 2 unread" }));
    await userEvent.click(await screen.findByRole("button", { name: "Mark all as read" }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/notifications/read-all")).toHaveLength(1),
    );
  });
});

describe("notifications page (FR-NOT-001)", () => {
  it("lists notifications and can show unread ones only", async () => {
    stub.routes["GET /bff/api/v1/notifications"] = () =>
      page([
        notification({
          id: "0192f3a4-0000-7000-8000-00000000c0e1",
          template_key: "export.ready",
          resource_type: "export",
          resource_id: "0192f3a4-0000-7000-8000-00000000e001",
          title: "Export ready",
        }),
        notification({
          id: "0192f3a4-0000-7000-8000-00000000c0e2",
          template_key: "document.quarantined",
          resource_type: "document",
          resource_id: "0192f3a4-0000-7000-8000-00000000d001",
          title: "File blocked",
        }),
        notification({
          id: "0192f3a4-0000-7000-8000-00000000c0e3",
          template_key: "announcement.new",
          resource_type: "announcement",
          resource_id: "0192f3a4-0000-7000-8000-00000000a001",
          title: "New message from SchoolOS",
        }),
      ]);
    renderWithIntl(<NotificationsScreen />);
    // The export opens its screen.
    expect(await screen.findByRole("link", { name: /Export ready/ })).toHaveAttribute(
      "href",
      expect.stringContaining("/exports/0192f3a4-0000-7000-8000-00000000e001"),
    );
    // A blocked file opens its document screen, which explains why (FR-DOC-002).
    expect(screen.getByRole("link", { name: /File blocked/ })).toHaveAttribute(
      "href",
      expect.stringContaining("/documents/0192f3a4-0000-7000-8000-00000000d001"),
    );
    // Announcements have no school screen: no link, a plain "mark as read" button instead.
    expect(
      screen.getByRole("button", { name: "Mark as read: New message from SchoolOS" }),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Show unread only" }));
    await waitFor(() =>
      expect(
        stub
          .callsTo("GET /bff/api/v1/notifications")
          .some((call) => call.url.searchParams.get("unread") === "true"),
      ).toBe(true),
    );
  });
});
