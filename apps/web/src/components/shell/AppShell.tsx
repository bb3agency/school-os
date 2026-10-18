"use client";

import { useTranslations } from "next-intl";
import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { CommandPalette, type CommandItem } from "@/components/console/CommandPalette";
import { Icon, type IconName } from "@/components/ui/Icon";
import { Kbd } from "@/components/ui/Kbd";
import { Menu } from "@/components/ui/Menu";
import {
  activeHref,
  type NavItem,
  type NavSection,
  type SidebarTheme,
} from "@/components/ui/SidebarNav";
import { ToastProvider } from "@/components/ui/Toaster";
import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { useDialogClose } from "@/lib/dialog-motion";
import { Wordmark } from "./Brand";
import { canvasFor, type ShellCanvas } from "./canvas";
import { useDensity } from "./density";
import { useOnScreenKeyboard } from "./keyboard";
import { MotionProvider } from "./MotionProvider";
import { Sidebar } from "./Sidebar";
import { sidebarControlClasses, sidebarThemes } from "./sidebar-theme";
import { useSidebarCollapsed } from "./sidebar-state";
import { SkipLink } from "./SkipLink";

const topbars: Record<
  SidebarTheme,
  { bar: string; menuButton: string; search: string; crumb: string }
> = {
  // School: the top bar sits on the canvas with a hairline under it; no card.
  school: {
    bar: "border-b border-border bg-canvas text-ink",
    menuButton: "border-border-soft bg-surface text-ink hover:bg-surface-muted",
    search: "border-border-soft bg-surface text-ink-muted hover:bg-surface-muted hover:text-ink",
    crumb: "text-ink-muted",
  },
  // Operator console: the dark neutral strip meets the dark sidebar, with a 2px amber marker
  // along its top edge, so an operator always knows they are in the control plane (C14;
  // owner decision 2026-10-18, report §12.5).
  platform: {
    bar: "platform-chrome border-t-2 border-b border-t-platform-accent border-b-platform-hover bg-platform text-platform-ink",
    menuButton: "border-platform-hover bg-platform text-platform-ink hover:bg-platform-hover",
    search: "border-platform-hover bg-platform-hover text-platform-muted hover:text-platform-ink",
    crumb: "text-platform-muted",
  },
};

/** A quick action for the command palette (already filtered by permission). */
export interface ShellAction {
  id: string;
  label: string;
  href: string;
  icon?: IconName;
}

export interface AppShellProps {
  theme?: SidebarTheme;
  /** Link target of the wordmark ("/" or "/platform"). */
  homeHref: string;
  /** Accessible name of the navigation ("Main", "Platform"). */
  navLabel: string;
  /** Permission-filtered navigation groups (empty groups are dropped). */
  sections: readonly NavSection[];
  /** Palette "Actions" (permission-filtered by the console that passes them). */
  actions?: readonly ShellAction[];
  /**
   * Phone bottom bar (below md) for roles with a phone-first job: teachers, principals.
   * At most four items, each an existing page from `sections`.
   */
  bottomBar?: readonly NavItem[];
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
  /** The canvas behind the page (docs/17 §3.2): one flat canvas; kept for older callers. */
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

/** Ctrl+K (Windows, Linux) or ⌘K (macOS), never while an input method composes. */
function isPaletteKey(event: KeyboardEvent): boolean {
  return (
    (event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    !event.shiftKey &&
    !event.isComposing &&
    (event.key === "k" || event.key === "K" || event.code === "KeyK")
  );
}

/** Ctrl+\ toggles the compact sidebar (closes docs/17 §8 "no keyboard shortcut"). */
function isSidebarKey(event: KeyboardEvent): boolean {
  return (
    (event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    !event.isComposing &&
    (event.key === "\\" || event.code === "Backslash")
  );
}

/**
 * Console layout (docs/17 §5.2): ONE sidebar, a top bar and the page.
 *
 * - lg and up: the sidebar is a sticky, full-height column at the left, 240px (compact 56px:
 *   icons, labels in a tooltip on hover and focus, still the accessible names; Ctrl+\ or its
 *   button; remembered on this computer).
 * - below lg: the "Menu" button opens the same Sidebar as a modal drawer on the native
 *   `<dialog>`; only one "Main" navigation landmark ever exists.
 * - Top bar (48px): where you are (section › page, from the same `sections`), the
 *   "Search or jump to" button (Ctrl/⌘+K opens the command palette over the same
 *   permission-filtered items plus `actions`), page-wide tools and the preferences menu
 *   (row density).
 * - Phones: an optional bottom bar of up to four destinations (`bottomBar`).
 *
 * Keyboard: the skip link comes first. Gutters come from `.shell-gutter` and `.shell-frame`
 * (docs/17 §5.1). Print: chrome hidden.
 */
export function AppShell({
  theme = "school",
  homeHref,
  navLabel,
  sections,
  actions = [],
  bottomBar = [],
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
  const tu = useTranslations("ui");
  const pathname = usePathname() ?? "";
  const [open, setOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [collapsed, toggleCollapsed] = useSidebarCollapsed();
  const [density, setDensity] = useDensity();
  const [tip, setTip] = useState<Tip | null>(null);
  // The phone bottom bar steps aside while the on-screen keyboard is up (docs/17 §5.7).
  const keyboardOpen = useOnScreenKeyboard();
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDialogElement>(null);
  const sidebarRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const searchButtonRef = useRef<HTMLButtonElement>(null);
  const drawerId = useId();
  const topbar = topbars[theme];
  const surface = canvas ?? canvasFor(theme, pathname);
  const visible = sections.filter((section) => section.items.length > 0);

  // Where you are: the section and the nav item that is the current page (the same
  // most-specific match the sidebar marks).
  const crumbs = useMemo(() => {
    const items = visible.flatMap((section) => section.items);
    const current = activeHref(pathname, items);
    if (!current) return null;
    const section = visible.find((entry) => entry.items.some((item) => item.href === current));
    const item = items.find((entry) => entry.href === current);
    if (!section || !item) return null;
    return { section: section.label, item, onItem: pathname.replace(/\/+$/, "") === item.href };
  }, [visible, pathname]);

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

  // Shortcuts: Ctrl/⌘+K opens the palette (it overrides the browser's own Ctrl+K on these
  // pages, owner decision 2026-10-18); Ctrl+\ switches the compact sidebar.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isPaletteKey(event)) {
        event.preventDefault();
        setPaletteOpen(true);
      } else if (isSidebarKey(event)) {
        event.preventDefault();
        toggleCollapsed();
        setTip(null);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [toggleCollapsed]);

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

  // The palette lists exactly what the sidebar lists (same permission and feature filter),
  // then the console's quick actions, then preferences.
  const groups = [
    tu("palette.groupPages"),
    tu("palette.groupActions"),
    tu("palette.groupPreferences"),
  ] as const;
  const commands: CommandItem[] = [
    ...visible.flatMap((section) =>
      section.items.map((item): CommandItem => ({
        id: `go:${item.href}`,
        label: item.label,
        group: groups[0],
        href: item.href,
        hint: section.label,
        keywords: section.label,
        ...(item.icon ? { icon: item.icon } : {}),
      })),
    ),
    ...actions.map((action): CommandItem => ({
      id: `do:${action.id}`,
      label: action.label,
      group: groups[1],
      href: action.href,
      icon: action.icon ?? "plus",
    })),
    {
      id: "pref:density",
      label:
        density === "compact" ? tu("palette.densityComfortable") : tu("palette.densityCompact"),
      group: groups[2],
      icon: "layers",
      onRun: () => setDensity(density === "compact" ? "comfortable" : "compact"),
    },
    {
      id: "pref:sidebar",
      label: tu("palette.toggleSidebar"),
      group: groups[2],
      icon: collapsed ? "panelLeftOpen" : "panelLeftClose",
      shortcut: "Ctrl \\",
      onRun: () => toggleCollapsed(),
    },
  ];

  return (
    <MotionProvider>
      <ToastProvider>
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
              "sticky top-0 hidden h-viewport w-60 shrink-0 lg:block collapsed:w-14",
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
                  aria-keyshortcuts="Control+Backslash"
                  data-tooltip={collapseLabel}
                  className={sidebarControlClasses(theme)}
                >
                  <Icon name="panelLeftClose" className="size-4.5 collapsed:hidden" />
                  <Icon name="panelLeftOpen" className="hidden size-4.5 collapsed:block" />
                </button>
              }
            />
          </div>

          <div className="flex min-w-0 flex-1 flex-col print:block">
            <header
              className={cn("shell-topbar shell-gutter sticky top-0 z-30", topbar.bar)}
              data-print="hide"
            >
              <div className="shell-frame flex min-h-12 flex-wrap items-center gap-x-3 gap-y-2 py-1.5">
                <div className="flex min-w-0 flex-1 items-center gap-3 md:flex-none lg:hidden">
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
                      "pressable inline-flex min-h-11 shrink-0 items-center gap-2 rounded-md border px-3 text-sm font-semibold",
                      topbar.menuButton,
                    )}
                  >
                    <Icon name="menu" className="size-4.5" />
                    {t("menu")}
                  </button>
                  <span className="flex min-w-0 items-center gap-2.5">
                    <Wordmark tone={theme} textClassName="max-sm:sr-only" />
                    {brandBadge}
                  </span>
                </div>
                {crumbs ? (
                  <nav
                    aria-label={tu("shell.breadcrumb")}
                    className="hidden min-w-0 flex-1 md:block"
                  >
                    {/* Tablets (md to lg) show only the page, on one line; phones leave it to
                        the page heading (docs/17 §5.7). */}
                    <ol
                      className={cn(
                        "flex min-w-0 items-center gap-1.5 text-sm lg:flex-wrap",
                        topbar.crumb,
                      )}
                    >
                      <li className="max-lg:hidden">{crumbs.section}</li>
                      <li aria-hidden="true" className="max-lg:hidden">
                        <Icon name="chevronRight" className="size-3.5" />
                      </li>
                      <li
                        className="min-w-0 max-lg:truncate lg:break-anywhere"
                        title={crumbs.item.label}
                      >
                        {crumbs.onItem ? (
                          <span
                            aria-current="page"
                            className={cn(
                              "font-semibold",
                              theme === "platform" ? "text-platform-ink" : "text-ink",
                            )}
                          >
                            {crumbs.item.label}
                          </span>
                        ) : (
                          <Link
                            href={crumbs.item.href}
                            className={cn(
                              "rounded-sm font-semibold underline-offset-4 hover:underline",
                              theme === "platform" ? "text-platform-ink" : "text-accent",
                            )}
                          >
                            {crumbs.item.label}
                          </Link>
                        )}
                      </li>
                    </ol>
                  </nav>
                ) : (
                  <div className="hidden flex-1 md:block" />
                )}
                <div className="ms-auto flex min-w-0 flex-wrap items-center justify-end gap-2">
                  <button
                    ref={searchButtonRef}
                    type="button"
                    aria-haspopup="dialog"
                    aria-keyshortcuts="Control+K Meta+K"
                    onClick={() => setPaletteOpen(true)}
                    className={cn(
                      "pressable inline-flex min-h-9 items-center justify-center gap-2 rounded-md border px-2.5 text-sm pointer-coarse:min-h-11 pointer-coarse:min-w-11",
                      topbar.search,
                    )}
                  >
                    <Icon name="search" className="size-4" />
                    <span className="max-md:sr-only">{tu("palette.trigger")}</span>
                    <Kbd className="max-md:hidden">Ctrl K</Kbd>
                  </button>
                  {topbarActions}
                  <Menu
                    label={tu("shell.preferences")}
                    variant="icon"
                    triggerClassName={
                      theme === "platform"
                        ? "text-platform-muted hover:bg-platform-hover hover:text-platform-ink"
                        : undefined
                    }
                    trigger={<Icon name="settings" className="size-4.5" />}
                    entries={[
                      { kind: "label", id: "density-label", label: tu("shell.density") },
                      {
                        kind: "radio",
                        id: "comfortable",
                        label: tu("shell.comfortable"),
                        checked: density === "comfortable",
                        onSelect: () => setDensity("comfortable"),
                      },
                      {
                        kind: "radio",
                        id: "compact",
                        label: tu("shell.compact"),
                        checked: density === "compact",
                        onSelect: () => setDensity("compact"),
                      },
                      { kind: "separator", id: "sep" },
                      {
                        id: "palette",
                        label: tu("shell.shortcutPalette"),
                        icon: "search",
                        onSelect: () => setPaletteOpen(true),
                      },
                    ]}
                  />
                </div>
              </div>
            </header>
            <div
              className={cn(
                "shell-gutter flex-1 print:p-0",
                bottomBar.length > 0 && "max-md:pb-16",
              )}
            >
              <main
                id="main"
                tabIndex={-1}
                className="shell-frame min-w-0 pt-4 pb-8 focus:outline-none print:p-0"
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

          <CommandPalette
            open={paletteOpen}
            onClose={() => {
              setPaletteOpen(false);
              const trigger = searchButtonRef.current;
              if (trigger && document.activeElement === document.body) trigger.focus();
            }}
            items={commands}
            groups={groups}
          />

          {bottomBar.length > 0 ? (
            <nav
              aria-label={tu("shell.quickLinks")}
              data-print="hide"
              // Hidden while the on-screen keyboard is up: it would cover the field, the
              // SaveBar or the Ask composer (docs/17 §5.7).
              hidden={keyboardOpen}
              className={cn(
                "bottom-bar fixed inset-x-0 bottom-0 z-30 border-t md:hidden",
                theme === "platform"
                  ? "platform-chrome border-platform-hover bg-platform"
                  : "border-border bg-surface",
              )}
            >
              <ul className="grid auto-cols-fr grid-flow-col">
                {bottomBar.slice(0, 4).map((item) => {
                  const current = activeHref(pathname, bottomBar) === item.href;
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        aria-current={current ? "page" : undefined}
                        className={cn(
                          "flex min-h-14 flex-col items-center justify-center gap-0.5 px-1 text-xs",
                          theme === "platform"
                            ? current
                              ? "font-semibold text-platform-accent"
                              : "text-platform-muted"
                            : current
                              ? "font-semibold text-accent"
                              : "text-ink-muted",
                        )}
                      >
                        {item.icon ? <Icon name={item.icon} className="size-5" /> : null}
                        <span className="text-center break-anywhere">{item.label}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </nav>
          ) : null}

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
      </ToastProvider>
    </MotionProvider>
  );
}
