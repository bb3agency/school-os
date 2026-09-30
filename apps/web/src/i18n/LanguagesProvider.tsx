"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { Locale } from "./routing";

/**
 * The UI languages that are switched on (ADR-0036), for client components. The locale layout
 * fills it from the server-side switch (`enabledLocales()` in ./languages.ts); client code
 * never reads the environment itself. Without a provider only English is on, so a screen
 * rendered anywhere else (tests, previews) shows no Telugu unless asked to.
 */
const LanguagesContext = createContext<readonly Locale[]>(["en"]);

export function LanguagesProvider({
  locales,
  children,
}: {
  locales: readonly Locale[];
  children: ReactNode;
}) {
  return <LanguagesContext.Provider value={locales}>{children}</LanguagesContext.Provider>;
}

/** The UI languages people may use, English first. */
export function useEnabledLocales(): readonly Locale[] {
  return useContext(LanguagesContext);
}

/**
 * True only when Telugu is switched on. Screens use it to show Telugu fields, columns,
 * previews and options; with it off they show English only.
 */
export function useTeluguEnabled(): boolean {
  return useContext(LanguagesContext).includes("te");
}
