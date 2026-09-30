"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { cssEasing, EASE_OUT } from "@/lib/motion";

/** Marketing reveals may run longer than console UI (explanatory, once per visit). */
const DURATION = 600;
/** 30–80ms between items of a group. */
const STAGGER = 60;
/** Reveal once the block's top passes 85% of the viewport height (an IntersectionObserver root margin). */
export const REVEAL_MARGIN = "0px 0px -15% 0px";

const HIDDEN: Keyframe = { opacity: 0, transform: "translateY(16px)" };
const SHOWN: Keyframe = { opacity: 1, transform: "none" };
const REDUCE = "(prefers-reduced-motion: reduce)";

/**
 * Scroll reveal for marketing sections (docs/17 §5.6), CSP-safe and progressive:
 *
 * - The server renders the content **visible**, with no style attribute (a server-rendered
 *   `style` would be blocked by the nonce CSP, which has no 'unsafe-inline' for styles).
 * - After hydration, only content that is still **below the fold** is held in its start state
 *   by a Web Animations API animation (no style attribute is ever written, so the CSP allows
 *   it) and revealed once when it scrolls into view. Content already on screen never blinks
 *   or moves. When an animation ends or is cancelled, the element is simply its visible self.
 * - It reveals when its top passes 85% of the viewport height (a root margin, not a share of
 *   its own area), so a block taller than the screen still reveals at 400% zoom or on a
 *   short phone in landscape (WCAG 1.4.10). Keyboard focus landing inside it reveals it at
 *   once (no motion on the keyboard path, never an invisible focused control).
 * - No JavaScript, no Web Animations API, no IntersectionObserver or reduced motion (also
 *   when switched on while the page is open): nothing is hidden, ever.
 * - Print resets it (marketing.css).
 * - No Motion import: the page does not pay for Motion's animation engine to fade a few
 *   blocks (the phone menu uses the small `m` + `domAnimation` bundle).
 *
 * `stagger` reveals the direct children one after another instead of the whole block.
 */
export function Reveal({
  children,
  className,
  stagger = false,
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  stagger?: boolean;
  as?: "div" | "ul" | "ol";
}) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const root = ref.current;
    if (
      !root ||
      typeof IntersectionObserver === "undefined" ||
      typeof root.animate !== "function" ||
      typeof window.matchMedia !== "function"
    )
      return;
    const reduce = window.matchMedia(REDUCE);
    if (reduce.matches || root.getBoundingClientRect().top < window.innerHeight) return;

    const targets = stagger ? (Array.from(root.children) as HTMLElement[]) : [root];
    // Hold the start state (fill: forwards) until the block scrolls into view.
    const animations: Animation[] = targets.map((target) =>
      target.animate([HIDDEN, HIDDEN], { duration: 0, fill: "forwards" }),
    );
    let revealed = false;
    /** Everything visible at once: cancelled animations leave the elements as they are. */
    const showNow = () => {
      revealed = true;
      for (const animation of animations.splice(0)) animation.cancel();
    };
    const reveal = () => {
      if (revealed) return;
      showNow();
      targets.forEach((target, index) => {
        animations.push(
          target.animate([HIDDEN, SHOWN], {
            duration: DURATION,
            delay: stagger ? index * STAGGER : 0,
            easing: cssEasing(EASE_OUT),
            // Hidden while it waits for its stagger delay; its own visible self afterwards.
            fill: "backwards",
          }),
        );
      });
    };

    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        observer.disconnect();
        reveal();
      },
      { rootMargin: REVEAL_MARGIN, threshold: 0 },
    );
    observer.observe(root);
    const onFocus = () => {
      if (!revealed) showNow();
    };
    // Reduced motion switched on while the page is open: stop, show everything.
    const onReduce = () => {
      if (reduce.matches) showNow();
    };
    root.addEventListener("focusin", onFocus);
    reduce.addEventListener("change", onReduce);
    return () => {
      observer.disconnect();
      root.removeEventListener("focusin", onFocus);
      reduce.removeEventListener("change", onReduce);
      // Leaving the page half-way: never leave anything invisible behind.
      showNow();
    };
  }, [stagger]);

  return (
    <Tag ref={ref as never} data-reveal="" className={cn(className)}>
      {children}
    </Tag>
  );
}
