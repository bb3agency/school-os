import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ACTIVITY_EVENT, forgetSessionInfo, refreshSessionInfo } from "@/lib/bff/session-client";
import { messages, renderWithIntl } from "@/test/render";
import { IDLE_WARNING_MS, SessionControls } from "./SessionControls";

const CSRF = "k".repeat(43);
const MINUTE = 60_000;
let expiresInMs: number;
let idleTimeoutMs: number;
let absoluteInMs: number;
let logoutRedirect: string;
let requests: Request[];

function info() {
  return Response.json({
    authenticated: true,
    kind: "staff",
    display_name: "Office Clerk",
    active_tenant_id: null,
    mfa: false,
    csrf_token: CSRF,
    idle_timeout_ms: idleTimeoutMs,
    idle_expires_at: new Date(Date.now() + expiresInMs).toISOString(),
    absolute_expires_at: new Date(Date.now() + absoluteInMs).toISOString(),
    expires_in_ms: expiresInMs,
  });
}

beforeEach(() => {
  forgetSessionInfo();
  requests = [];
  expiresInMs = 15 * MINUTE;
  idleTimeoutMs = 15 * MINUTE;
  absoluteInMs = 12 * 60 * MINUTE;
  logoutRedirect = "/signed-out";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const request = new Request(new URL(input, "http://localhost:3000"), init);
      requests.push(request);
      const path = new URL(request.url).pathname;
      if (path === "/bff/auth/session") {
        if (request.method === "POST") expiresInMs = idleTimeoutMs;
        return info();
      }
      if (path === "/bff/auth/logout") return Response.json({ redirect_to: logoutRedirect });
      return new Response(null, { status: 404 });
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

describe("SessionControls (docs/07 §5.2 shared-PC mode)", () => {
  it("shows who is signed in and a visible Lock now button that signs out at once", async () => {
    const navigate = vi.fn();
    logoutRedirect = "https://idp.example/staff/endsession?client_id=staff-client";
    renderWithIntl(
      <SessionControls kind="staff" displayName="Office Clerk" navigate={navigate} />,
      "te",
    );
    expect(screen.getByText("Office Clerk గా సైన్ ఇన్ అయ్యారు")).toBeInTheDocument();
    const lock = screen.getByRole("button", { name: messages.te.auth.lockNow });
    expect(lock).toBeVisible();
    await act(async () => {
      fireEvent.click(lock);
    });
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith(logoutRedirect));
    const logout = requests.find((r) => r.url.includes("/bff/auth/logout"));
    expect(logout?.method).toBe("POST");
    expect(logout?.headers.get("x-csrf-token")).toBe(CSRF);
  });

  it("operators get a plain Sign out button", () => {
    renderWithIntl(<SessionControls kind="operator" navigate={vi.fn()} tone="dark" />);
    expect(screen.getByRole("button", { name: messages.en.auth.signOut })).toBeInTheDocument();
  });

  it("warns 1 minute before the idle timeout and signs out when nobody answers", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    expiresInMs = 5 * MINUTE;
    const navigate = vi.fn();
    renderWithIntl(<SessionControls kind="staff" navigate={navigate} />);
    await flush();
    const dialog = screen.getByRole("dialog", { hidden: true });
    expect(dialog).not.toHaveAttribute("open");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * MINUTE - IDLE_WARNING_MS - 1_000);
    });
    expect(dialog).not.toHaveAttribute("open");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(dialog).toHaveAttribute("open");
    expect(screen.getByText(messages.en.auth.idle.title)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(IDLE_WARNING_MS);
    });
    expect(navigate).toHaveBeenCalledWith("/signed-out?reason=idle");
    expect(requests.some((r) => r.url.includes("/bff/auth/logout"))).toBe(true);
  });

  it("'Stay signed in' slides the timeout on the server and closes the warning", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    expiresInMs = 2 * MINUTE;
    const navigate = vi.fn();
    renderWithIntl(<SessionControls kind="staff" navigate={navigate} />);
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MINUTE + 1_000);
    });
    const dialog = screen.getByRole("dialog");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: messages.en.auth.idle.stay }));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(dialog).not.toHaveAttribute("open");
    const keepAlive = requests.find(
      (r) => r.method === "POST" && r.url.includes("/bff/auth/session"),
    );
    expect(keepAlive?.headers.get("x-csrf-token")).toBe(CSRF);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * MINUTE);
    });
    expect(navigate).not.toHaveBeenCalled();
  });

  it("BFF activity pushes the deadline back", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    expiresInMs = 2 * MINUTE;
    const navigate = vi.fn();
    renderWithIntl(<SessionControls kind="staff" navigate={navigate} />);
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    act(() => {
      window.dispatchEvent(new CustomEvent(ACTIVITY_EVENT, { detail: { kind: "staff" } }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * MINUTE);
    });
    expect(screen.getByRole("dialog", { hidden: true })).not.toHaveAttribute("open");
    expect(navigate).not.toHaveBeenCalled();
  });

  it("follows the school's idle timeout from the session info (FR-IAM-003)", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    idleTimeoutMs = 5 * MINUTE;
    expiresInMs = 5 * MINUTE;
    const navigate = vi.fn();
    renderWithIntl(<SessionControls kind="staff" navigate={navigate} />);
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2 * MINUTE);
    });
    // Activity slides the deadline by the school's 5 minutes, not the 15 minute default.
    act(() => {
      window.dispatchEvent(new CustomEvent(ACTIVITY_EVENT, { detail: { kind: "staff" } }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * MINUTE - IDLE_WARNING_MS + 1_000);
    });
    expect(screen.getByRole("dialog")).toHaveAttribute("open");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IDLE_WARNING_MS);
    });
    expect(navigate).toHaveBeenCalledWith("/signed-out?reason=idle");
  });

  it("times a new idle timeout as soon as the session info is refreshed (FR-TEN-012)", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const navigate = vi.fn();
    renderWithIntl(<SessionControls kind="staff" navigate={navigate} />);
    await flush();
    // The school lowered its timeout to 5 minutes; the server applied it (GET /me).
    idleTimeoutMs = 5 * MINUTE;
    expiresInMs = 5 * MINUTE;
    await act(async () => {
      await refreshSessionInfo("staff");
    });
    expect(requests.filter((r) => new URL(r.url).pathname === "/bff/auth/session")).toHaveLength(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * MINUTE - IDLE_WARNING_MS + 1_000);
    });
    expect(screen.getByRole("dialog")).toHaveAttribute("open");
    // Activity now slides by 5 minutes, not the 15 it started with.
    act(() => {
      window.dispatchEvent(new CustomEvent(ACTIVITY_EVENT, { detail: { kind: "staff" } }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * MINUTE);
    });
    expect(navigate).toHaveBeenCalledWith("/signed-out?reason=idle");
  });

  it("never counts activity past the absolute session limit", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    idleTimeoutMs = 30 * MINUTE;
    expiresInMs = 10 * MINUTE;
    absoluteInMs = 10 * MINUTE;
    const navigate = vi.fn();
    renderWithIntl(<SessionControls kind="staff" navigate={navigate} />);
    await flush();
    act(() => {
      window.dispatchEvent(new CustomEvent(ACTIVITY_EVENT, { detail: { kind: "staff" } }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * MINUTE);
    });
    expect(navigate).toHaveBeenCalledWith("/signed-out?reason=idle");
  });
});
