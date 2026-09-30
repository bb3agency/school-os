"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SidebarNav, type NavSection, type SidebarTheme } from "@/components/ui/SidebarNav";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { BrandMark } from "./Brand";

/** Sidebar chrome per theme (docs/17 §5.2, contrast pairs in §7). */
export const sidebarThemes: Record<
  SidebarTheme,
  { surface: string; brand: string; control: string; divider: string }
> = {
  school: {
    surface: "bg-surface text-ink",
    brand: "text-ink hover:bg-surface-muted",
    control: "text-ink-muted hover:bg-surface-muted hover:text-ink",
    divider: "border-border",
  },
  platform: {
    // .platform-chrome switches the focus ring to yellow on the dark violet.
    surface: "platform-chrome bg-platform text-platform-ink",
    brand: "text-platform-ink hover:bg-platform-hover",
    control: "text-platform-muted hover:bg-platform-hover hover:text-platform-ink",
    divider: "border-platform-hover",
  },
};

/** Round 40px icon button for the sidebar header (collapse toggle, drawer close). */
export function sidebarControlClasses(theme: SidebarTheme, size: "md" | "lg" = "md"): string {
  return cn(
    "inline-flex shrink-0 items-center justify-center rounded-md transition-colors",
    size === "lg" ? "size-11" : "size-10",
    sidebarThemes[theme].control,
  );
}

export interface SidebarProps {
  /** "inline": the wide-screen sidebar (may be compact); "drawer": inside the menu dialog. */
  mode: "inline" | "drawer";
  theme: SidebarTheme;
  homeHref: string;
  /** Accessible name of the navigation ("Main", "Platform"). */
  navLabel: string;
  sections: readonly NavSection[];
  /** Under the wordmark: the school and "Switch school", or the platform badge. */
  context?: ReactNode;
  /** Bottom of the sidebar: who is signed in and "Lock now" / "Sign out". */
  account?: ReactNode;
  /** Header button: the collapse toggle (inline) or the close button (drawer). */
  control: ReactNode;
}

/**
 * The one sidebar (docs/17 §5.2), top to bottom: brand, school (or platform) context, the
 * grouped navigation (scrolls on its own when it is taller than the screen) and the signed-in
 * account. AppShell renders it beside the page from lg and in the menu drawer below lg.
 * In the drawer the wordmark is plain text, so the close button is the first focus stop.
 */
export function Sidebar({
  mode,
  theme,
  homeHref,
  navLabel,
  sections,
  context,
  account,
  control,
}: SidebarProps) {
  const t = useTranslations("common");
  const styles = sidebarThemes[theme];
  const wordmark = (
    <>
      <BrandMark tone={theme} />
      <span className="min-w-0 text-lg font-semibold collapsed:sr-only">{t("appName")}</span>
    </>
  );
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 px-3 pt-4 pb-3 collapsed:flex-col collapsed:gap-3">
        {mode === "inline" ? (
          <Link
            href={homeHref}
            data-tooltip={t("appName")}
            className={cn(
              "flex min-h-10 min-w-0 flex-1 items-center gap-2.5 rounded-md px-1.5 py-1 transition-colors",
              "collapsed:flex-none collapsed:px-0.5",
              styles.brand,
            )}
          >
            {wordmark}
          </Link>
        ) : (
          <p className="flex min-w-0 flex-1 items-center gap-2.5 px-1.5 py-1">{wordmark}</p>
        )}
        {control}
      </div>
      {context ? <div className="px-3 pb-3">{context}</div> : null}
      <SidebarNav
        label={navLabel}
        sections={sections}
        theme={theme}
        className={cn(
          "min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 pt-1 pb-4",
          // The menu scrolls on its own; its edge shows where the account area starts.
          account ? cn("border-b", styles.divider) : undefined,
        )}
      />
      {account ? <div className="px-3 py-3">{account}</div> : null}
    </div>
  );
}
