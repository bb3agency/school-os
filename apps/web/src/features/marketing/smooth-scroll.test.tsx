import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Lenis on the home page only (docs/17 §5.6): reduced motion, anchors, cleanup. */

const instances: Array<{
  options: Record<string, unknown>;
  scrollTo: ReturnType<typeof vi.fn>;
  destroy: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  start: ReturnType<typeof vi.fn>;
  isScrolling: boolean | "native" | "smooth";
}> = [];

vi.mock("lenis", () => ({
  default: class {
    options: Record<string, unknown>;
    scrollTo = vi.fn((_target: HTMLElement, opts?: { onComplete?: () => void }) =>
      opts?.onComplete?.(),
    );
    destroy = vi.fn();
    stop = vi.fn();
    start = vi.fn();
    isScrolling: boolean | "native" | "smooth" = false;
    constructor(options: Record<string, unknown>) {
      this.options = options;
      instances.push(this);
    }
  },
}));

const { SmoothScroll } = await import("./SmoothScroll");

let reduce = false;
let coarse = false;
const listeners = new Set<() => void>();
beforeEach(() => {
  instances.length = 0;
  reduce = false;
  coarse = false;
  listeners.clear();
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    get matches() {
      return query.includes("reduce") ? reduce : query.includes("coarse") ? coarse : false;
    },
    media: query,
    addEventListener: (_: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
  }));
});
afterEach(() => {
  // @ts-expect-error -- jsdom has no matchMedia; restore its absence.
  delete window.matchMedia;
});

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function page() {
  return render(
    <div>
      <a href="#how">How</a>
      <a href="#main">Skip</a>
      <section id="how">Steps</section>
      <SmoothScroll />
    </div>,
  );
}

describe("SmoothScroll (Lenis, home page only)", () => {
  it("ends a running wheel glide when a scroll key is pressed, so the key's scroll wins", async () => {
    const view = page();
    await settle();
    const lenis = instances[0]!;
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "PageDown" }));
    expect(lenis.stop).not.toHaveBeenCalled();
    lenis.isScrolling = "smooth";
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
    expect(lenis.stop).not.toHaveBeenCalled();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "PageDown" }));
    expect(lenis.stop).toHaveBeenCalledTimes(1);
    expect(lenis.start).toHaveBeenCalledTimes(1);
    view.unmount();
  });

  it("is never created on a touch screen (phones keep native scrolling)", async () => {
    coarse = true;
    const view = page();
    await settle();
    expect(instances).toHaveLength(0);
    view.unmount();
  });

  it("smooths the wheel only, and is destroyed on unmount", async () => {
    const view = page();
    await settle();
    expect(instances).toHaveLength(1);
    expect(instances[0]!.options).toMatchObject({
      smoothWheel: true,
      syncTouch: false,
      anchors: false,
    });
    view.unmount();
    expect(instances[0]!.destroy).toHaveBeenCalled();
  });

  it("is never created under prefers-reduced-motion, and stops when it is switched on", async () => {
    reduce = true;
    const first = page();
    await settle();
    expect(instances).toHaveLength(0);
    first.unmount();

    reduce = false;
    page();
    await settle();
    expect(instances).toHaveLength(1);
    reduce = true;
    act(() => listeners.forEach((cb) => cb()));
    expect(instances[0]!.destroy).toHaveBeenCalled();
  });

  it("a pointer click on an in-page link glides there and moves focus to the target", async () => {
    const view = page();
    await settle();
    const link = view.getByText("How");
    const event = new MouseEvent("click", { bubbles: true, cancelable: true, detail: 1 });
    link.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    const target = view.container.querySelector("#how") as HTMLElement;
    expect(instances[0]!.scrollTo).toHaveBeenCalledWith(target, expect.any(Object));
    expect(document.activeElement).toBe(target);
    expect(target.getAttribute("tabindex")).toBe("-1");
    expect(window.location.hash).toBe("#how");
  });

  it("keyboard activation and the skip link keep the browser's own jump", async () => {
    const view = page();
    await settle();
    const keyboard = new MouseEvent("click", { bubbles: true, cancelable: true, detail: 0 });
    view.getByText("How").dispatchEvent(keyboard);
    expect(keyboard.defaultPrevented).toBe(false);
    const skip = new MouseEvent("click", { bubbles: true, cancelable: true, detail: 1 });
    view.getByText("Skip").dispatchEvent(skip);
    expect(skip.defaultPrevented).toBe(false);
    expect(instances[0]!.scrollTo).not.toHaveBeenCalled();
  });
});
