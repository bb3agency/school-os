"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import type { NavSection, SidebarTheme } from "@/components/ui/SidebarNav";
import { usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { useDialogClose } from "@/lib/dialog-motion";
import { BrandMark } from "./Brand";
import { canvasFor, type ShellCanvas } from "./canvas";
import { MotionProvider } from "./MotionProvider";
import { Sidebar } from "./Sidebar";
import { sidebarControlClasses, sidebarThemes } from "./sidebar-theme";
import { useSidebarCollapsed } from "./sidebar-state";
import { SkipLink } from "./SkipLink";

const topbars: Record<SidebarTheme, { bar: string; menuButton: string }> = {
  // School: the top bar sits on the canvas, no card: the sidebar is the chrome.
  school: {
    bar: "text-ink",
    menuButton: "border-border-soft bg-surface text-ink hover:bg-surface-muted",
  },
  // Platform: a dark violet strip that meets the dark sidebar, so an operator always knows
  // they are in the control plane (C14).
  platform: {
    bar: "platform-chrome bg-platform text-platform-ink",
    menuButton: "border-platform-hover bg-platform text-platform-ink hover:bg-platform-hover",
  },
};

export interface AppShellProps {
  theme?: SidebarTheme;
  /** Link target of the wordmark ("/" or "/platform"). */
  homeHref: string;
  /** Accessible name of the navigation ("Main", "Platform"). */
  navLabel: string;
  /** Permission-filtered navigation groups (empty groups are dropped). */
  sections: readonly NavSection[];
  /** Sidebar, under the wordmark: the school and "Switch school", or the platform badge. */
  context?: ReactNode;
  /** Sidebar, bottom: who is signed in and "Lock now" / "Sign out". */
  account?: ReactNode;
  /** Top bar, beside the wordmark below lg (e.g. the platform badge). */
  brandBadge?: ReactNode;
  /** Top bar, right: page-wide tools (notification bell, language switch). */
  topbarActions?: ReactNode;
  /**
   * Mounted once whatever the width (e.g. the idle-timeout warning), never inside the
   * sidebar: the wide-screen sidebar is hidden below lg and the drawer renders only while
   * open.
   */
  session?: ReactNode;
  /** Announcements and status banners above the page. */
  banner?: ReactNode;
  /**
   * The canvas behind the page (docs/17 §3.2). By default `canvasFor(theme, path)`: the
   * gradient on the dashboards, the near-neutral canvas on dense record screens.
   */
  canvas?: ShellCanvas;
  children: ReactNode;
}

/** At this width and up the sidebar sits beside the page (Tailwind `lg`). */
export const SIDEBAR_MEDIA_QUERY = "(min-width: 64rem)";

/** The compact sidebar's label: its text, where it goes, and the page it belongs to. */
interface Tip {
  text: string;
  top: number;
  left: number;
  path: string;
}

/**
 * Console layout (docs/17 §5.2): ONE sidebar and the page.
 *
 * - lg and up: the sidebar is a sticky, full-height column at the left (brand, school,
 *   grouped navigation that scrolls on its own, signed-in account). Its collapse button
 *   makes it compact (icons only, labels in a tooltip on hover and focus, still the
 *   accessible names); the choice is remembered on this computer (sidebar-state.ts).
 * - below lg: the "Menu" button in the top bar opens the same Sidebar as a modal drawer on
 *   the native `<dialog>`: focus moves to its close button and stays inside, Escape, the
 *   close button, a tap on the dimmed page, following a link or widening the window close
 *   it, focus returns to the button, and the page behind is inert. The drawer renders its
 *   content only while open, and the wide sidebar is hidden below lg, so there is only ever
 *   one "Main" navigation landmark.
 *
 * The top bar holds page-wide tools (bell, language). Keyboard: the skip link comes first.
 * Gutters come from `.shell-gutter` and `.shell-frame` (docs/17 §5.1). Print: chrome hidden.
 * The canvas (gradient or near-neutral) follows `canvasFor` unless `canvas` is given.
 */
export function AppShell({
  theme = "school",
  homeHref,
  navLabel,
  sections,
  context,
  account,
  brandBadge,
  topbarActions,
  session,
  banner,
  canvas,
  children,
}: AppShellProps) {
  const t = useTranslations("shell");
  const tc = useTranslations("common");
  const pathname = usePathname() ?? "";
  const [open, setOpen] = useState(false);
  const [collapsed, toggleCollapsed] = useSidebarCollapsed();
  const [tip, setTip] = useState<Tip | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDialogElement>(null);
  const sidebarRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const drawerId = useId();
  const topbar = topbars[theme];
  const surface = canvas ?? canvasFor(theme, pathname);
  const visible = sections.filter((section) => section.items.length > 0);

  // The drawer slides back out the way it came in (instant under reduced motion); Escape too.
  const closeMenu = useDialogClose(drawerRef, "drawer");

  // A navigation closes the drawer (the close event returns focus to the menu button).
  useEffect(() => {
    closeMenu();
  }, [pathname, closeMenu]);

  // The drawer's content renders when it opens: then focus goes to its close button.
  useEffect(() => {
    if (open) closeButtonRef.current?.focus();
  }, [open]);

  // A click on the dimmed backdrop lands on the <dialog> box itself (the sheet fills the
  // rest), so it closes the drawer; so does growing the window to the width with the sidebar.
  useEffect(() => {
    const drawer = drawerRef.current;
    if (!drawer) return;
    const onClick = (event: MouseEvent) => {
      if (event.target === drawer) closeMenu();
    };
    drawer.addEventListener("click", onClick);
    const wide =
      typeof window.matchMedia === "function" ? window.matchMedia(SIDEBAR_MEDIA_QUERY) : null;
    const onWide = (event: MediaQueryListEvent) => {
      if (event.matches) closeMenu();
    };
    wide?.addEventListener("change", onWide);
    return () => {
      drawer.removeEventListener("click", onClick);
      wide?.removeEventListener("change", onWide);
    };
  }, [closeMenu]);

  // Compact sidebar: show an item's label beside it on hover and keyboard focus (WCAG 1.4.13:
  // it stays while the pointer is over the label, and Escape dismisses it).
  useEffect(() => {
    const sidebar = sidebarRef.current;
    if (!sidebar || !collapsed) return;
    const show = (event: Event) => {
      const target = (event.target as Element | null)?.closest<HTMLElement>("[data-tooltip]");
      if (!target || !sidebar.contains(target)) {
        setTip(null);
        return;
      }
      const rect = target.getBoundingClientRect();
      setTip({
        text: target.dataset.tooltip ?? "",
        top: rect.top + rect.height / 2,
        left: rect.right,
        path: pathname,
      });
    };
    const hide = (event: Event) => {
      const next = (event as FocusEvent | PointerEvent).relatedTarget as Node | null;
      if (next && tipRef.current?.contains(next)) return;
      setTip(null);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setTip(null);
    };
    const onScroll = () => setTip(null);
    sidebar.addEventListener("pointerover", show);
    sidebar.addEventListener("pointerleave", hide);
    sidebar.addEventListener("focusin", show);
    sidebar.addEventListener("focusout", hide);
    sidebar.addEventListener("scroll", onScroll, true);
    document.addEventListener("keydown", onKey);
    return () => {
      sidebar.removeEventListener("pointerover", show);
      sidebar.removeEventListener("pointerleave", hide);
      sidebar.removeEventListener("focusin", show);
      sidebar.removeEventListener("focusout", hide);
      sidebar.removeEventListener("scroll", onScroll, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [collapsed, pathname]);

  // The label shows only in the compact sidebar and only on the page it was opened on.
  const shownTip = collapsed && tip !== null && tip.path === pathname ? tip : null;

  // Place the label (CSSOM, allowed by the CSP; no style attribute is ever rendered).
  useLayoutEffect(() => {
    const node = tipRef.current;
    if (!node || !shownTip) return;
    node.style.top = `${shownTip.top}px`;
    node.style.left = `${shownTip.left}px`;
  }, [shownTip]);

  const collapseLabel = collapsed ? t("expand") : t("collapse");

  return (
    <MotionProvider>
      <div
        data-canvas={surface}
        className={cn("flex min-h-viewport", surface === "neutral" && "canvas-neutral")}
      >
        <SkipLink label={tc("skipToContent")} />

        {/* The sidebar beside the page (lg and up). */}
        <div
          ref={sidebarRef}
          data-sidebar-mode="inline"
          data-print="hide"
          className={cn(
            "sticky top-0 hidden h-viewport w-68 shrink-0 lg:block collapsed:w-18",
            theme === "school" && "border-e border-border",
            sidebarThemes[theme].surface,
          )}
        >
          <Sidebar
            mode="inline"
            theme={theme}
            homeHref={homeHref}
            navLabel={navLabel}
            sections={visible}
            context={context}
            account={account}
            control={
              <button
                type="button"
                onClick={() => {
                  toggleCollapsed();
                  setTip(null);
                }}
                aria-label={collapseLabel}
                data-tooltip={collapseLabel}
                className={sidebarControlClasses(theme)}
              >
                <Icon name="panelLeftClose" className="size-5 collapsed:hidden" />
                <Icon name="panelLeftOpen" className="hidden size-5 collapsed:block" />
              </button>
            }
          />
        </div>

        <div className="flex min-w-0 flex-1 flex-col print:block">
          <header className={cn("shell-gutter", topbar.bar)} data-print="hide">
            <div className="shell-frame flex flex-wrap items-center gap-x-3 gap-y-2 py-3">
              <div className="flex min-w-0 flex-1 items-center gap-3 lg:hidden">
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
                    "pressable inline-flex min-h-11 shrink-0 items-center gap-2 rounded-full border px-3 text-sm font-semibold",
                    topbar.menuButton,
                  )}
                >
                  <Icon name="menu" className="size-4.5" />
                  {t("menu")}
                </button>
                <span className="flex min-w-0 items-center gap-2.5">
                  <BrandMark tone={theme} className="max-sm:hidden" />
                  <span className="text-lg font-semibold">{tc("appName")}</span>
                  {brandBadge}
                </span>
              </div>
              {topbarActions ? (
                <div className="ms-auto flex min-w-0 flex-wrap items-center justify-end gap-2 sm:gap-3">
                  {topbarActions}
                </div>
              ) : null}
            </div>
          </header>
          <div className="shell-gutter flex-1 print:p-0">
            <main
              id="main"
              tabIndex={-1}
              className="shell-frame min-w-0 pt-1 pb-8 focus:outline-none print:p-0"
            >
              {banner}
              {children}
            </main>
          </div>
        </div>

        {/* Menu drawer below lg (native modal dialog) with the same Sidebar. Its content renders
          only while open, so the page never has two "Main" navigation landmarks. */}
        <dialog
          ref={drawerRef}
          id={drawerId}
          aria-label={t("menu")}
          onClose={() => {
            setOpen(false);
            menuButtonRef.current?.focus();
          }}
          className={cn("drawer", theme === "platform" && sidebarThemes.platform.surface)}
          data-print="hide"
        >
          <div className="drawer-sheet">
            {open ? (
              <Sidebar
                mode="drawer"
                theme={theme}
                homeHref={homeHref}
                navLabel={navLabel}
                sections={visible}
                context={context}
                account={account}
                control={
                  <button
                    ref={closeButtonRef}
                    type="button"
                    onClick={closeMenu}
                    aria-label={t("closeMenu")}
                    className={cn(sidebarControlClasses(theme, "lg"), "rounded-full")}
                  >
                    <Icon name="close" className="size-5" />
                  </button>
                }
              />
            ) : null}
          </div>
        </dialog>

        {/* Compact sidebar label (visual only; see the effect above). */}
        <div
          ref={tipRef}
          aria-hidden="true"
          hidden={shownTip === null}
          className="sidebar-tip"
          data-print="hide"
          onPointerLeave={() => setTip(null)}
        >
          <span className="sidebar-tip-bubble">{shownTip?.text}</span>
        </div>

        {session}
      </div>
    </MotionProvider>
  );
}
