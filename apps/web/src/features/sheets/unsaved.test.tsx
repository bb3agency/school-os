import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetUnsavedGuardForTesting, useUnsavedChangesWarning } from "./unsaved";

/**
 * Browser Back/Forward with unsaved sheet changes (US-701 AC5; docs/17 §4.1). A stand-in for
 * the Next.js router listens to `popstate` on `window` (bubble phase, registered first, like
 * the App Router) and records every history change it would render.
 */

const MESSAGE = "You have changes that are not saved. Leave this page?";
let routed: string[];
const router = () => routed.push(window.location.pathname);

beforeEach(() => {
  routed = [];
  window.history.replaceState({ __NA: true, page: "previous" }, "", "/previous");
  window.history.pushState({ __NA: true, page: "sheet" }, "", "/documents/d1/sheet");
  window.addEventListener("popstate", router);
});

afterEach(() => {
  window.removeEventListener("popstate", router);
  resetUnsavedGuardForTesting();
  vi.restoreAllMocks();
});

function back(): Promise<void> {
  return new Promise((resolve) => {
    window.addEventListener("popstate", () => setTimeout(resolve, 0), { once: true });
    window.history.back();
  });
}

describe("useUnsavedChangesWarning and the Back button", () => {
  it("leaves history alone while nothing is unsaved", async () => {
    const length = window.history.length;
    const confirm = vi.spyOn(window, "confirm");
    renderHook(() => useUnsavedChangesWarning(false, MESSAGE));
    expect(window.history.length).toBe(length);
    await back();
    expect(confirm).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe("/previous");
    expect(routed).toEqual(["/previous"]);
  });

  it("asks on Back and stays on the page on Cancel, the router never sees it", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderHook(() => useUnsavedChangesWarning(true, MESSAGE));
    // One guard entry: same URL, the router's state kept.
    expect(window.history.state).toMatchObject({ __NA: true, page: "sheet" });
    window.history.back();
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(MESSAGE));
    expect(window.location.pathname).toBe("/documents/d1/sheet");
    expect(window.history.state).toMatchObject({ __NA: true, page: "sheet" });
    expect(routed).toEqual([]);
    // Still guarded: a second Back asks again.
    window.history.back();
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(2));
    expect(window.location.pathname).toBe("/documents/d1/sheet");
  });

  it("goes back on OK, with no second question from beforeunload", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderHook(() => useUnsavedChangesWarning(true, MESSAGE));
    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe("/previous"));
    expect(routed).toContain("/previous");
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  });

  it("a jump of several entries asks too; OK hands the move to the router", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderHook(() => useUnsavedChangesWarning(true, MESSAGE));
    window.history.go(-2); // e.g. from the Back button's long-press list
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(window.location.pathname).toBe("/documents/d1/sheet");
    expect(routed).toEqual([]);
    confirm.mockReturnValue(true);
    window.history.go(-2);
    await waitFor(() => expect(routed).toEqual(["/previous"]));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("two warnings on one page share one guard entry", () => {
    const length = window.history.length;
    renderHook(() => {
      useUnsavedChangesWarning(true, MESSAGE);
      useUnsavedChangesWarning(true, "Your open cell has a change. Leave?");
    });
    expect(window.history.length).toBe(length + 1);
  });

  it("after saving, one Back still leaves the page without asking", async () => {
    const confirm = vi.spyOn(window, "confirm");
    const { rerender, unmount } = renderHook(
      ({ active }) => useUnsavedChangesWarning(active, MESSAGE),
      { initialProps: { active: true } },
    );
    const length = window.history.length;
    rerender({ active: false });
    rerender({ active: true }); // typing again re-uses the guard entry
    expect(window.history.length).toBe(length);
    rerender({ active: false });
    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe("/previous"));
    expect(confirm).not.toHaveBeenCalled();
    unmount();
  });

  it("cleans up on unmount: no warning on close and no question on Back", async () => {
    const confirm = vi.spyOn(window, "confirm");
    const { unmount } = renderHook(() => useUnsavedChangesWarning(true, MESSAGE));
    unmount();
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe("/previous"));
    expect(confirm).not.toHaveBeenCalled();
  });
});
