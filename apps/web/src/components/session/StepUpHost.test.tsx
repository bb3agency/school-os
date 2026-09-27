import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { forgetSessionInfo } from "@/lib/bff/session-client";
import { STEP_UP_CHANNEL, STEP_UP_COMPLETE, stepUpHandler } from "@/lib/bff/step-up";
import { renderWithIntl } from "@/test/render";
import { StepUpHost } from "./StepUpHost";

/** Session info as the BFF reports it; `name` changes when someone else signs in. */
function stubSession(names: string[]) {
  let call = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      const name = names[Math.min(call, names.length - 1)];
      call += 1;
      return Response.json({
        authenticated: true,
        kind: "staff",
        display_name: name,
        active_tenant_id: "0192f3a4-0000-7000-8000-000000000001",
        mfa: true,
        csrf_token: "csrf",
        idle_timeout_ms: 900_000,
        idle_expires_at: new Date(Date.now() + 900_000).toISOString(),
        absolute_expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        expires_in_ms: 900_000,
      });
    }),
  );
}

afterEach(() => {
  forgetSessionInfo();
  vi.unstubAllGlobals();
});

function announceComplete() {
  const channel = new BroadcastChannel(STEP_UP_CHANNEL);
  channel.postMessage(STEP_UP_COMPLETE);
  channel.close();
}

describe("StepUpHost (ADR-0018, SEC-005)", () => {
  it("opens the sign-in window and confirms when it reports completion", async () => {
    stubSession(["Office Clerk"]);
    const open = vi.fn(() => ({}) as Window);
    const navigate = vi.fn();
    renderWithIntl(<StepUpHost open={open} navigate={navigate} />);
    const handler = stepUpHandler("staff");
    expect(handler).toBeDefined();
    let answer: Promise<boolean> = Promise.resolve(false);
    await act(async () => {
      answer = handler?.("/bff/auth/step-up?next=%2Fen%2Ffindings") ?? answer;
    });
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    expect(dialog).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Sign in again" }));
    expect(open).toHaveBeenCalledWith("/bff/auth/step-up?next=%2Fen%2Fstep-up-complete");
    expect(await screen.findByText(/Finish signing in in the other window/)).toBeInTheDocument();
    announceComplete();
    await expect(answer).resolves.toBe(true);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("Cancel answers false (nothing is retried)", async () => {
    stubSession(["Office Clerk"]);
    renderWithIntl(<StepUpHost open={vi.fn()} navigate={vi.fn()} />);
    let answer: Promise<boolean> = Promise.resolve(true);
    await act(async () => {
      answer = stepUpHandler("staff")?.("/bff/auth/step-up") ?? answer;
    });
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await expect(answer).resolves.toBe(false);
  });

  it("offers the full-page step-up when the window is blocked", async () => {
    stubSession(["Office Clerk"]);
    const navigate = vi.fn();
    renderWithIntl(<StepUpHost open={() => null} navigate={navigate} />);
    await act(async () => {
      void stepUpHandler("staff")?.("/bff/auth/step-up?next=%2Fen%2Ffindings");
    });
    await userEvent.click(await screen.findByRole("button", { name: "Sign in again" }));
    expect(await screen.findByText("The sign-in window was blocked")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Continue in this tab" }));
    expect(navigate).toHaveBeenCalledWith("/bff/auth/step-up?next=%2Fen%2Ffindings");
  });

  it("never replays the action when someone else signed in at the prompt", async () => {
    stubSession(["Office Clerk", "Someone Else"]);
    const navigate = vi.fn();
    renderWithIntl(<StepUpHost open={() => ({}) as Window} navigate={navigate} />);
    let answer: Promise<boolean> = Promise.resolve(true);
    await act(async () => {
      answer = stepUpHandler("staff")?.("/bff/auth/step-up") ?? answer;
    });
    await userEvent.click(await screen.findByRole("button", { name: "Sign in again" }));
    await userEvent.click(await screen.findByRole("button", { name: "I have signed in" }));
    await expect(answer).resolves.toBe(false);
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/en"));
  });

  it("unregisters when the page unmounts", () => {
    stubSession(["Office Clerk"]);
    const { unmount } = renderWithIntl(<StepUpHost open={vi.fn()} navigate={vi.fn()} />);
    expect(stepUpHandler("staff")).toBeDefined();
    unmount();
    expect(stepUpHandler("staff")).toBeUndefined();
  });
});
