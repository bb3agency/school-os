"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import {
  activeSectionId,
  SidebarNav,
  type NavSection,
  type SidebarTheme,
} from "@/components/ui/SidebarNav";
import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { SkipLink } from "./SkipLink";

const themes: Record<
  SidebarTheme,
  {
    rail: string;
    railLink: string;
    railActive: string;
    logo: string;
    topbar: string;
    menuButton: string;
  }
> = {
  school: {
    rail: "border-r border-border bg-surface",
    railLink: "border-border-soft text-ink-muted hover:bg-surface-muted hover:text-ink",
    railActive: "border-action bg-action text-on-action",
    logo: "bg-action text-on-action",
    topbar: "border-border bg-surface text-ink",
    menuButton: "border-border-soft bg-surface text-ink hover:bg-surface-muted",
  },
  platform: {
    rail: "platform-chrome bg-platform",
    railLink:
      "border-platform-hover text-platform-muted hover:bg-platform-hover hover:text-platform-ink",
    railActive: "border-platform-accent bg-platform-accent text-platform-accent-ink",
    logo: "bg-platform-accent text-platform-accent-ink",
    topbar: "platform-chrome border-platform bg-platform text-platform-ink",
    menuButton: "border-platform-hover bg-platform text-platform-ink hover:bg-platform-hover",
  },
};

export interface AppShellProps {
  theme?: SidebarTheme;
  /** Wordmark (and badge) at the left of the top bar. */
  brand: ReactNode;
  /** Link target of the round logo in the rail ("/" or "/platform"). */
  homeHref: string;
  /** Accessible name of the list navigation ("Main", "Platform"). */
  navLabel: string;
  /** Permission-filtered navigation groups (empty groups are dropped). */
  sections: readonly NavSection[];
  /** Session controls, notification bell, "Switch school", language switch. */
  headerActions?: ReactNode;
  /** Announcements and status banners above the page. */
  banner?: ReactNode;
  children: ReactNode;
}

/**
 * Console layout on the gradient canvas: a slim icon rail (one round button per section,
 * md and up), a white list panel with section headings (lg and up; a "Menu" button shows
 * it on smaller screens), a top bar card and the main landmark. Keyboard: skip link
 * first, Escape closes the menu, focus returns to the menu button.
 */
export function AppShell({
  theme = "school",
  brand,
  homeHref,
  navLabel,
  sections,
  headerActions,
  banner,
  children,
}: AppShellProps) {
  const t = useTranslations("common");
  const pathname = usePathname() ?? "";
  const [open, setOpen] = useState(false);
  const [openedAt, setOpenedAt] = useState(pathname);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const styles = themes[theme];
  const visible = sections.filter((section) => section.items.length > 0);
  const currentSection = activeSectionId(pathname, visible);

  // Close the menu after a navigation (render-time reset, no effect needed).
  if (open && openedAt !== pathname) {
    setOpen(false);
    setOpenedAt(pathname);
  }

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        menuButtonRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="flex min-h-screen">
      <SkipLink label={t("skipToContent")} />

      {/* Rail: section shortcuts (md and up). */}
      <div
        className={cn(
          "sticky top-0 hidden h-screen w-18 shrink-0 flex-col items-center gap-3 py-4 md:flex",
          styles.rail,
        )}
        data-print="hide"
      >
        <Link
          href={homeHref}
          aria-label={t("appName")}
          title={t("appName")}
          className={cn(
            "flex size-10 items-center justify-center rounded-full font-display text-xl",
            styles.logo,
          )}
        >
          <span aria-hidden="true">S</span>
        </Link>
        <nav aria-label={t("sectionsNav")} className="mt-2">
          <ul className="flex flex-col items-center gap-2">
            {visible.map((section) => {
              const active = section.id === currentSection;
              const first = section.items[0];
              if (!first) return null;
              return (
                <li key={section.id}>
                  <Link
                    href={first.href}
                    aria-label={section.label}
                    title={section.label}
                    aria-current={active ? "true" : undefined}
                    className={cn(
                      "flex size-10 items-center justify-center rounded-full border transition-colors",
                      active ? styles.railActive : styles.railLink,
                    )}
                  >
                    <Icon name={section.icon} className="size-5" />
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>

      <div className="flex min-w-0 flex-1 gap-4 p-3 md:p-4 print:block print:p-0">
        {/* List panel: always in the DOM (and in the accessibility tree once shown). */}
        <aside
          id={panelId}
          className={cn(
            "w-64 shrink-0 self-start rounded-xl border border-border bg-surface p-3 shadow-card",
            "lg:sticky lg:top-4 lg:block lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto",
            open
              ? "fixed inset-x-3 top-3 bottom-3 z-40 block w-auto overflow-y-auto md:static md:inset-auto md:w-64"
              : "hidden",
            "lg:bottom-auto",
          )}
          data-print="hide"
        >
          <div className="mb-3 flex items-center justify-between gap-2 px-1 lg:hidden">
            <p className="font-semibold text-ink">{t("appName")}</p>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                menuButtonRef.current?.focus();
              }}
              aria-label={t("closeMenu")}
              className="inline-flex size-9 items-center justify-center rounded-full border border-border-soft text-ink hover:bg-surface-muted"
            >
              <Icon name="close" className="size-4.5" />
            </button>
          </div>
          <SidebarNav label={navLabel} sections={visible} theme={theme} />
        </aside>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <header
            className={cn(
              "flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-2.5 shadow-card",
              styles.topbar,
            )}
            data-print="hide"
          >
            <div className="flex min-w-0 items-center gap-3">
              <button
                ref={menuButtonRef}
                type="button"
                aria-expanded={open}
                aria-controls={panelId}
                onClick={() => {
                  setOpenedAt(pathname);
                  setOpen((value) => !value);
                }}
                className={cn(
                  "inline-flex min-h-10 items-center gap-2 rounded-full border px-3 text-sm font-medium lg:hidden",
                  styles.menuButton,
                )}
              >
                <Icon name="menu" className="size-4.5" />
                {t("menu")}
              </button>
              {brand}
            </div>
            <div className="flex flex-wrap items-center justify-end gap-3">{headerActions}</div>
          </header>
          <main
            id="main"
            tabIndex={-1}
            className="min-w-0 flex-1 pb-8 focus:outline-none print:p-0"
          >
            {banner}
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
