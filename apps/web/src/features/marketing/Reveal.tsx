"use client";

import { useAnimate, useReducedMotion, inView } from "motion/react";
import { useEffect, type ReactNode } from "react";
import { cn } from "@/lib/cn";

/** Strong ease-out (design-engineering skills): starts fast, settles softly. */
const EASE_OUT = [0.23, 1, 0.32, 1] as const;
const DURATION = 0.6;
/** 30–80ms between items of a group. */
const STAGGER = 0.06;

/**
 * Scroll reveal for marketing sections (docs/17 §5.5), CSP-safe and progressive:
 *
 * - The server renders the content **visible**, with no style attribute (a server-rendered
 *   `style` would be blocked by the nonce CSP, which has no 'unsafe-inline' for styles).
 * - After hydration, only content that is still **below the fold** is set to its start state
 *   through Motion's imperative `animate` (the CSSOM, which CSP allows), and revealed once
 *   when it scrolls into view. Content already on screen never blinks or moves.
 * - No JavaScript, reduced motion or no IntersectionObserver: nothing is hidden, ever.
 * - Print resets it (marketing.css).
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
  const [scope, animate] = useAnimate<HTMLElement>();
  const reduce = useReducedMotion();

  useEffect(() => {
    const root = scope.current;
    if (reduce || !root || typeof IntersectionObserver === "undefined") return;
    if (root.getBoundingClientRect().top < window.innerHeight) return;
    const targets = stagger ? (Array.from(root.children) as HTMLElement[]) : [root];
    for (const target of targets) {
      animate(target, { opacity: 0, transform: "translateY(16px)" }, { duration: 0 });
    }
    const stop = inView(
      root,
      () => {
        targets.forEach((target, index) => {
          animate(
            target,
            { opacity: 1, transform: "translateY(0px)" },
            { duration: DURATION, ease: EASE_OUT, delay: stagger ? index * STAGGER : 0 },
          );
        });
      },
      { amount: 0.15 },
    );
    return () => {
      stop();
      // Leaving the page half-way: never leave anything invisible behind.
      for (const target of targets) {
        target.style.opacity = "";
        target.style.transform = "";
      }
    };
  }, [animate, reduce, scope, stagger]);

  return (
    <Tag ref={scope as never} data-reveal="" className={cn(className)}>
      {children}
    </Tag>
  );
}
