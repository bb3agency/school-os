"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
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

/** At this width and up the list panel sits beside the page (Tailwind `lg`). */
export const PANEL_MEDIA_QUERY = "(min-width: 64rem)";

/**
 * Console layout on the gradient canvas: a slim icon rail (one round button per section,
 * md and up), a white list panel with section headings (lg and up), a top bar card and the
 * main landmark. Below lg the "Menu" button opens the same list as a modal drawer on the
 * native `<dialog>`: focus moves into it and stays there, Escape, the close button or a tap
 * on the dimmed page closes it, focus returns to the button, and the page behind is inert.
 * Page gutters (with safe-area insets) and the content width come from `.shell-gutter` and
 * `.shell-frame` in globals.css (docs/17 §5.1). Keyboard: the skip link comes first.
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
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDialogElement>(null);
  const drawerId = useId();
  const styles = themes[theme];
  const visible = sections.filter((section) => section.items.length > 0);
  const currentSection = activeSectionId(pathname, visible);

  const closeMenu = useCallback(() => {
    const drawer = drawerRef.current;
    if (drawer?.open) drawer.close();
  }, []);

  // A navigation closes the drawer (the close event returns focus to the menu button).
  useEffect(() => {
    closeMenu();
  }, [pathname, closeMenu]);

  // A click on the dimmed backdrop lands on the <dialog> box itself (the sheet fills the
  // rest), so it closes the drawer; so does growing the window to the width with the panel.
  useEffect(() => {
    const drawer = drawerRef.current;
    if (!drawer) return;
    const onClick = (event: MouseEvent) => {
      if (event.target === drawer) closeMenu();
    };
    drawer.addEventListener("click", onClick);
    const wide =
      typeof window.matchMedia === "function" ? window.matchMedia(PANEL_MEDIA_QUERY) : null;
    const onWide = (event: MediaQueryListEvent) => {
      if (event.matches) closeMenu();
    };
    wide?.addEventListener("change", onWide);
    return () => {
      drawer.removeEventListener("click", onClick);
      wide?.removeEventListener("change", onWide);
    };
  }, [closeMenu]);

  return (
    <div className="flex min-h-viewport">
      <SkipLink label={t("skipToContent")} />

      {/* Rail: section shortcuts (md and up). */}
      <div
        className={cn(
          "sticky top-0 hidden h-viewport w-18 shrink-0 flex-col items-center gap-3 py-4 md:flex",
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

      <div className="shell-gutter min-w-0 flex-1 py-3 md:py-4 print:block print:p-0">
        <div className="shell-frame flex gap-4 print:block">
          {/* List panel (lg and up); below lg the same list opens in the drawer. */}
          <aside
            className={cn(
              "hidden w-64 shrink-0 self-start rounded-xl border border-border bg-surface p-3 shadow-card",
              "lg:sticky lg:top-4 lg:block lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto",
            )}
            data-print="hide"
          >
            <SidebarNav label={navLabel} sections={visible} theme={theme} />
          </aside>

          <div className="flex min-w-0 flex-1 flex-col gap-3 md:gap-4">
            <header
              className={cn(
                "flex flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-xl border px-3 py-2 shadow-card sm:px-4 sm:py-2.5",
                styles.topbar,
              )}
              data-print="hide"
            >
              <div className="flex min-w-0 flex-1 items-center gap-3 sm:flex-none">
                <button
                  ref={menuButtonRef}
                  type="button"
                  aria-expanded={open}
                  aria-controls={drawerId}
                  aria-haspopup="dialog"
                  onClick={() => {
                    const drawer = drawerRef.current;
                    if (!drawer || drawer.open) return;
                    drawer.showModal();
                    setOpen(true);
                  }}
                  className={cn(
                    "inline-flex min-h-11 shrink-0 items-center gap-2 rounded-full border px-3 text-sm font-medium lg:hidden",
                    styles.menuButton,
                  )}
                >
                  <Icon name="menu" className="size-4.5" />
                  {t("menu")}
                </button>
                {brand}
              </div>
              {/* Phones: the actions take their own row under the menu button and brand. */}
              <div className="flex w-full min-w-0 flex-wrap items-center gap-2 sm:w-auto sm:justify-end sm:gap-3">
                {headerActions}
              </div>
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

      {/* Menu drawer below lg (native modal dialog). The list renders only while open, so
          the page never has two "Main" navigation landmarks. */}
      <dialog
        ref={drawerRef}
        id={drawerId}
        aria-label={t("menu")}
        onClose={() => {
          setOpen(false);
          menuButtonRef.current?.focus();
        }}
        className="drawer"
        data-print="hide"
      >
        <div className="drawer-sheet">
          <div className="mb-3 flex items-center justify-between gap-2 px-1">
            <p className="font-semibold text-ink">{t("appName")}</p>
            <button
              type="button"
              onClick={closeMenu}
              aria-label={t("closeMenu")}
              className="inline-flex size-11 shrink-0 items-center justify-center rounded-full border border-border-soft text-ink hover:bg-surface-muted"
            >
              <Icon name="close" className="size-4.5" />
            </button>
          </div>
          {open ? <SidebarNav label={navLabel} sections={visible} theme={theme} /> : null}
        </div>
      </dialog>
    </div>
  );
}
