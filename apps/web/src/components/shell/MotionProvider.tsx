"use client";

import { domAnimation, LazyMotion, MotionConfig } from "motion/react";
import type { ReactNode } from "react";
import { EASE_OUT, seconds } from "@/lib/motion";

const DEFAULT_TRANSITION = {
  duration: seconds("enter"),
  ease: [...EASE_OUT] as [number, number, number, number],
};

/**
 * Motion for the consoles (docs/17 §5.5), mounted once by AppShell.
 *
 * - `LazyMotion` with the small `domAnimation` bundle, `strict`: only the light `m.*`
 *   components are allowed (a stray `motion.*` import throws in development).
 * - `MotionConfig reducedMotion="user"`: with prefers-reduced-motion, transforms and layout
 *   changes jump to their end and only opacity still fades.
 * - Default transition: the shared 200ms strong ease-out (`lib/motion.ts`).
 *
 * CSP (SEC-010, no style attributes): a Motion element writes its `initial` values into a
 * `style` attribute when it is server-rendered. So `m.*` elements are used only for UI that
 * exists after a user action (a panel that opens, an item that appears), never in the
 * server HTML; see docs/17 §5.5 for the pattern.
 */
export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user" transition={DEFAULT_TRANSITION}>
        {children}
      </MotionConfig>
    </LazyMotion>
  );
}
