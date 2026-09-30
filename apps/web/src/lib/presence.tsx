"use client";

import { useIsPresent } from "motion/react";
import type { ReactNode } from "react";

/**
 * A direct child of `AnimatePresence` that tells its content whether it is leaving
 * (docs/17 §5.5). A panel that fades out stays in the DOM for its exit (150ms); render it
 * `inert` while `exiting`, so the fading panel can neither take focus nor catch a click meant
 * for the page underneath.
 */
export function Presence({ children }: { children: (exiting: boolean) => ReactNode }) {
  const present = useIsPresent();
  return children(!present);
}
