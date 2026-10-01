import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import type * as Navigation from "next/navigation";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { messages, renderWithIntl } from "@/test/render";
import { REVEAL_MARGIN, Reveal } from "./Reveal";
import { SiteHeader } from "./SiteHeader";

/**
 * Marketing motion (docs/17 §5.6; NFR-A11Y-001, SEC-010): the scroll reveal hides only content
 * below the fold, only with the Web Animations API and IntersectionObserver, never under
 * reduced motion, never for keyboard focus, and never writes a style attribute; the phone
 * menu is a keyboard-usable disclosure that closes when focus or a click leaves it.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/welcome",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useParams: () => ({ locale: "en" }),
  };
});

const m = messages.en.marketing;

/** A controllable prefers-reduced-motion / min-width media query list. */
function stubMedia(initial: { reduce?: boolean; wide?: boolean } = {}) {
  const state = { reduce: initial.reduce ?? false, wide: initial.wide ?? false };
  const lists = new Map<string, { listeners: Set<() => void>; query: string }>();
  const matches = (query: string) =>
    query.includes("prefers-reduced-motion")
      ? state.reduce
      : query.includes("min-width") && state.wide;
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => {
      const entry = lists.get(query) ?? { listeners: new Set<() => void>(), query };
      lists.set(query, entry);
      return {
        get matches() {
          return matches(query);
        },
        media: query,
        addEventListener: (_: string, listener: () => void) => entry.listeners.add(listener),
        removeEventListener: (_: string, listener: () => void) => entry.listeners.delete(listener),
      };
    }),
  );
  return {
    set(next: Partial<typeof state>) {
      Object.assign(state, next);
      for (const entry of lists.values()) for (const listener of entry.listeners) listener();
    },
  };
}

interface FakeAnimation {
  target: Element;
  keyframes: Keyframe[];
  options: KeyframeAnimationOptions;
  cancel: ReturnType<typeof vi.fn>;
}

describe("Reveal (CSP-safe scroll reveal)", () => {
  let animations: FakeAnimation[];
  let observers: {
    callback: IntersectionObserverCallback;
    options?: IntersectionObserverInit | undefined;
    observed: Element[];
    disconnect: ReturnType<typeof vi.fn>;
  }[];
  let top: number;

  beforeEach(() => {
    animations = [];
    observers = [];
    top = 2000; // below the fold of the 768px window
    vi.stubGlobal("innerHeight", 768);
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
      () => ({ top, bottom: top + 400, left: 0, right: 0, width: 0, height: 400 }) as DOMRect,
    );
    Element.prototype.animate = function animate(
      this: Element,
      keyframes: Keyframe[] | PropertyIndexedKeyframes | null,
      options?: number | KeyframeAnimationOptions,
    ) {
      const animation: FakeAnimation = {
        target: this,
        keyframes: keyframes as Keyframe[],
        options: options as KeyframeAnimationOptions,
        cancel: vi.fn(),
      };
      animations.push(animation);
      return animation as unknown as Animation;
    };
    class FakeObserver {
      observed: Element[] = [];
      disconnect = vi.fn();
      constructor(
        public callback: IntersectionObserverCallback,
        public options?: IntersectionObserverInit,
      ) {
        observers.push(this);
      }
      observe(target: Element) {
        this.observed.push(target);
      }
    }
    vi.stubGlobal("IntersectionObserver", FakeObserver);
  });

  afterEach(() => {
    delete (Element.prototype as Partial<Element>).animate;
    vi.unstubAllGlobals();
  });

  function list(stagger = true) {
    return render(
      <Reveal as="ul" stagger={stagger}>
        <li>
          <a href="#one">One</a>
        </li>
        <li>Two</li>
        <li>Three</li>
      </Reveal>,
    );
  }

  const intersect = (isIntersecting = true) =>
    act(() => {
      const observer = observers[0]!;
      observer.callback(
        observer.observed.map((target) => ({ target, isIntersecting })) as never,
        observer as never,
      );
    });

  it("the server HTML is visible content with no style attribute (nonce CSP, SEC-010)", () => {
    const html = renderToString(
      <Reveal stagger>
        <p>Visible</p>
      </Reveal>,
    );
    expect(html).toContain("Visible");
    expect(html).not.toMatch(/\sstyle=/);
    expect(html).toContain("data-reveal");
  });

  it("holds content below the fold, then reveals it with a stagger when it scrolls in", () => {
    stubMedia();
    const { container } = list();
    const items = [...container.querySelectorAll("li")];
    // Held hidden through the Web Animations API, never a style attribute.
    expect(animations.map((a) => a.target)).toEqual(items);
    for (const hold of animations) {
      expect(hold.options.fill).toBe("forwards");
      expect(hold.keyframes[0]).toMatchObject({ opacity: 0 });
    }
    expect(container.querySelectorAll("[style]")).toHaveLength(0);
    // Reachable at any zoom: a root margin, not a share of the block's own area.
    expect(observers[0]!.options).toMatchObject({ rootMargin: REVEAL_MARGIN, threshold: 0 });

    const holds = [...animations];
    intersect();
    for (const hold of holds) expect(hold.cancel).toHaveBeenCalled();
    const reveals = animations.slice(holds.length);
    expect(reveals.map((a) => a.target)).toEqual(items);
    expect(reveals.map((a) => a.options.delay)).toEqual([0, 60, 120]);
    for (const reveal of reveals) {
      expect(reveal.keyframes.at(-1)).toMatchObject({ opacity: 1 });
      expect(reveal.options.duration).toBe(600);
      expect(reveal.options.easing).toBe("cubic-bezier(0.23, 1, 0.32, 1)");
      // No fill forwards: when it ends the element is its own visible self.
      expect(reveal.options.fill).toBe("backwards");
    }
    expect(observers[0]!.disconnect).toHaveBeenCalled();
    // Once only.
    intersect();
    expect(animations).toHaveLength(holds.length + reveals.length);
  });

  it("without stagger the block reveals as one", () => {
    stubMedia();
    const { container } = list(false);
    expect(animations.map((a) => a.target)).toEqual([container.querySelector("ul")]);
  });

  it("content already on screen is never hidden (no blink on load)", () => {
    stubMedia();
    top = 400;
    list();
    expect(animations).toHaveLength(0);
    expect(observers).toHaveLength(0);
  });

  it("reduced motion, no IntersectionObserver or no Web Animations API: nothing is hidden", () => {
    stubMedia({ reduce: true });
    list().unmount();
    expect(animations).toHaveLength(0);

    stubMedia();
    vi.stubGlobal("IntersectionObserver", undefined);
    list().unmount();
    expect(animations).toHaveLength(0);

    stubMedia();
    delete (Element.prototype as Partial<Element>).animate;
    list();
    expect(animations).toHaveLength(0);
  });

  it("keyboard focus inside shows everything at once, without motion", () => {
    stubMedia();
    list();
    const holds = [...animations];
    fireEvent.focusIn(screen.getByRole("link", { name: "One" }));
    for (const hold of holds) expect(hold.cancel).toHaveBeenCalled();
    expect(animations).toHaveLength(holds.length);
    // Scrolling in later plays nothing.
    intersect();
    expect(animations).toHaveLength(holds.length);
  });

  it("reduced motion switched on while the page is open shows everything at once", () => {
    const media = stubMedia();
    list();
    const holds = [...animations];
    act(() => media.set({ reduce: true }));
    for (const hold of holds) expect(hold.cancel).toHaveBeenCalled();
    intersect();
    expect(animations).toHaveLength(holds.length);
  });

  it("unmounting half-way never leaves anything hidden", () => {
    stubMedia();
    const { unmount } = list();
    const holds = [...animations];
    unmount();
    for (const hold of holds) expect(hold.cancel).toHaveBeenCalled();
    expect(observers[0]!.disconnect).toHaveBeenCalled();
  });
});

describe("SiteHeader phone menu (disclosure, keyboard, WCAG 2.4.11)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function header() {
    stubMedia();
    return renderWithIntl(
      <>
        <SiteHeader current="home" contactEmail={null} />
        <main>
          <a href="#after">After the header</a>
        </main>
      </>,
    );
  }

  const menuButton = () => screen.getByRole("button", { name: m.nav.menu });
  const panelOf = (button: HTMLElement) =>
    document.getElementById(button.getAttribute("aria-controls") ?? "");

  it("renders closed on the server, with no style attribute", () => {
    const html = renderToString(
      <NextIntlClientProvider locale="en" messages={messages.englishOnly} timeZone="Asia/Kolkata">
        <SiteHeader current="home" contactEmail={null} />
      </NextIntlClientProvider>,
    );
    expect(html).not.toMatch(/\sstyle=/);
    expect(html).toContain('aria-expanded="false"');
    header();
    const button = menuButton();
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(panelOf(button)).toBeNull();
  });

  it("opens with focus on the first link; Escape closes and returns focus to the button", async () => {
    header();
    const button = menuButton();
    await userEvent.click(button);
    const close = screen.getByRole("button", { name: m.nav.closeMenu });
    expect(close).toHaveAttribute("aria-expanded", "true");
    const panel = panelOf(close);
    expect(panel).not.toBeNull();
    const first = panel!.querySelector("a");
    expect(first).toHaveTextContent(m.nav.features);
    expect(first).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    const reopened = menuButton();
    expect(reopened).toHaveAttribute("aria-expanded", "false");
    expect(reopened).toHaveFocus();
  });

  it("while it fades out the panel is inert (no focus, no clicks), then it is gone", async () => {
    header();
    await userEvent.click(menuButton());
    const close = screen.getByRole("button", { name: m.nav.closeMenu });
    const id = close.getAttribute("aria-controls") ?? "";
    await userEvent.click(close);
    const leaving = document.getElementById(id);
    expect(leaving).not.toBeNull();
    expect(leaving).toHaveAttribute("inert");
    await waitFor(() => expect(document.getElementById(id)).toBeNull());
  });

  it("Tab moving focus out of the header closes the menu (it would hide the focused control)", async () => {
    header();
    await userEvent.click(menuButton());
    expect(screen.getByRole("button", { name: m.nav.closeMenu })).toBeInTheDocument();
    act(() => screen.getByRole("link", { name: "After the header" }).focus());
    expect(menuButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("a click outside the header closes it; focus stays where it went", async () => {
    header();
    await userEvent.click(menuButton());
    const after = screen.getByRole("link", { name: "After the header" });
    await userEvent.click(after);
    expect(menuButton()).toHaveAttribute("aria-expanded", "false");
    expect(after).toHaveFocus();
  });

  it("following a link closes it", async () => {
    header();
    await userEvent.click(menuButton());
    const panel = panelOf(screen.getByRole("button", { name: m.nav.closeMenu }))!;
    const link = [...panel.querySelectorAll("a")].find((a) => a.textContent === m.nav.pricing)!;
    link.addEventListener("click", (event) => event.preventDefault());
    await userEvent.click(link);
    expect(menuButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("widening the window to lg closes it", async () => {
    const media = stubMedia();
    renderWithIntl(<SiteHeader current="home" contactEmail={null} />);
    await userEvent.click(menuButton());
    act(() => media.set({ wide: true }));
    expect(menuButton()).toHaveAttribute("aria-expanded", "false");
  });
});
