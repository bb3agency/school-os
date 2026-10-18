"use client";

/*
 * Menu button: dropdowns, the row "⋯" menu, the top bar's account menu (docs/17 §4.2).
 * WAI-ARIA APG menu button pattern on plain elements (no headless library: an ADR would be
 * needed). Written for SchoolOS after the Calevate rowMenu/dropdown reference
 * (docs/design/reference/calevate); no code from "Pixel Perfect".
 */

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "./Icon";
import { useKeepInViewport } from "./keep-in-view";

export type MenuEntry =
  | {
      kind?: "action";
      id: string;
      label: string;
      onSelect: () => void;
      icon?: IconName;
      /** Destructive actions (delete, revoke) read in the danger colour. */
      danger?: boolean;
      disabled?: boolean;
    }
  | { kind: "link"; id: string; label: string; href: string; icon?: IconName }
  | {
      kind: "radio";
      id: string;
      label: string;
      checked: boolean;
      onSelect: () => void;
      icon?: IconName;
    }
  | { kind: "separator"; id: string }
  | { kind: "label"; id: string; label: ReactNode };

export interface MenuProps {
  /** Accessible name of the trigger (and the menu), e.g. "Actions for Sample student A". */
  label: string;
  /**
   * What the trigger shows; defaults to a "⋯" icon. `icon` and `bare` triggers are named by
   * `label`; a `button` trigger by its visible text (WCAG 2.5.3).
   */
  trigger?: ReactNode;
  entries: readonly MenuEntry[];
  /** Panel edge aligned with the trigger. */
  align?: "start" | "end";
  /** Trigger look: `icon` (32px square, row menus), `button` (secondary button), `bare`. */
  variant?: "icon" | "button" | "bare";
  className?: string | undefined;
  triggerClassName?: string | undefined;
}

const triggerStyles: Record<NonNullable<MenuProps["variant"]>, string> = {
  icon: "pressable inline-flex size-8 items-center justify-center rounded-md text-ink-muted hover:bg-surface-muted hover:text-ink pointer-coarse:size-11",
  button:
    "pressable inline-flex min-h-10 items-center gap-2 rounded-md border border-border-soft bg-surface px-3 text-sm font-semibold text-ink hover:bg-surface-muted pointer-coarse:min-h-11",
  bare: "pressable inline-flex items-center gap-2 rounded-md",
};

function focusables(menu: HTMLElement | null): HTMLElement[] {
  if (!menu) return [];
  return Array.from(
    menu.querySelectorAll<HTMLElement>(
      '[role="menuitem"]:not([aria-disabled="true"]), [role="menuitemradio"]',
    ),
  );
}

/**
 * A button that opens a list of actions or links. Enter, Space or Down opens it on the first
 * item, Up on the last; inside, Up/Down/Home/End move, a letter jumps to the next item that
 * starts with it, Enter or Space chooses, Escape closes and returns focus to the button, Tab
 * closes and moves on. A click outside closes it. The panel appears only after a user action,
 * so its CSS entrance never runs on page load (and not at all under reduced motion).
 */
export function Menu({
  label,
  trigger,
  entries,
  align = "end",
  variant = "icon",
  className,
  triggerClassName,
}: MenuProps) {
  const [open, setOpen] = useState(false);
  const [initial, setInitial] = useState<"first" | "last">("first");
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  // On a narrow phone the panel is nudged back inside the screen (docs/17 §5.7).
  useKeepInViewport(menuRef, open);

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) buttonRef.current?.focus();
  }, []);

  // Focus the first (or last) item when the menu opens.
  useEffect(() => {
    if (!open) return;
    const items = focusables(menuRef.current);
    (initial === "last" ? items[items.length - 1] : items[0])?.focus();
  }, [open, initial]);

  // A pointer press outside closes it (focus stays where the press put it).
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) close(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, close]);

  const openAt = (where: "first" | "last") => {
    setInitial(where);
    setOpen(true);
  };

  const onTriggerKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      openAt("first");
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openAt("last");
    }
  };

  const onMenuKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = focusables(menuRef.current);
    const index = items.indexOf(document.activeElement as HTMLElement);
    const move = (to: number) => {
      event.preventDefault();
      items[(to + items.length) % items.length]?.focus();
    };
    switch (event.key) {
      case "ArrowDown":
        move(index + 1);
        break;
      case "ArrowUp":
        move(index - 1);
        break;
      case "Home":
        move(0);
        break;
      case "End":
        move(items.length - 1);
        break;
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        close(true);
        break;
      case "Tab":
        close(false);
        break;
      default:
        if (event.key.length === 1 && /\S/.test(event.key) && !event.ctrlKey && !event.metaKey) {
          const key = event.key.toLocaleLowerCase();
          const start = index + 1;
          for (let step = 0; step < items.length; step += 1) {
            const item = items[(start + step) % items.length];
            if (item?.textContent?.trim().toLocaleLowerCase().startsWith(key)) {
              event.preventDefault();
              item.focus();
              break;
            }
          }
        }
    }
  };

  const itemClasses = (danger = false, active = false) =>
    cn(
      "flex min-h-9 w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-start text-sm outline-none pointer-coarse:min-h-11",
      "hover:bg-surface-muted focus-visible:bg-accent-soft focus-visible:outline-none focus:bg-accent-soft",
      "aria-disabled:cursor-not-allowed aria-disabled:opacity-60",
      danger ? "text-danger" : active ? "font-semibold text-ink" : "text-ink",
    );

  return (
    <div ref={rootRef} className={cn("relative inline-flex", className)}>
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={variant === "button" ? undefined : label}
        onClick={() => (open ? close(false) : openAt("first"))}
        onKeyDown={onTriggerKey}
        className={cn(triggerStyles[variant], triggerClassName)}
      >
        {trigger ?? <Icon name="more" className="size-4.5" />}
      </button>
      {open ? (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={label}
          tabIndex={-1}
          onKeyDown={onMenuKey}
          className={cn(
            "overlay-panel menu-in absolute top-full z-40 mt-1 min-w-48 max-w-[min(20rem,calc(100vw-2rem))] p-1",
            align === "end" ? "end-0 origin-top-right" : "start-0 origin-top-left",
          )}
        >
          {entries.map((entry) => {
            if (entry.kind === "separator") {
              return <div key={entry.id} role="separator" className="my-1 h-px bg-border" />;
            }
            if (entry.kind === "label") {
              return (
                <div
                  key={entry.id}
                  role="presentation"
                  className="px-2.5 pt-1.5 pb-1 text-xs text-ink-subtle"
                >
                  {entry.label}
                </div>
              );
            }
            const icon = entry.icon ? <Icon name={entry.icon} className="size-4 shrink-0" /> : null;
            if (entry.kind === "link") {
              return (
                <Link
                  key={entry.id}
                  href={entry.href}
                  role="menuitem"
                  tabIndex={-1}
                  onClick={() => close(false)}
                  className={itemClasses()}
                >
                  {icon}
                  <span className="min-w-0 flex-1">{entry.label}</span>
                </Link>
              );
            }
            if (entry.kind === "radio") {
              return (
                <button
                  key={entry.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={entry.checked}
                  tabIndex={-1}
                  onClick={() => {
                    entry.onSelect();
                    close(true);
                  }}
                  className={itemClasses(false, entry.checked)}
                >
                  <span className="flex size-4 shrink-0 items-center justify-center">
                    {entry.checked ? <Icon name="check" className="size-4 text-accent" /> : null}
                  </span>
                  <span className="min-w-0 flex-1">{entry.label}</span>
                </button>
              );
            }
            return (
              <button
                key={entry.id}
                type="button"
                role="menuitem"
                tabIndex={-1}
                aria-disabled={entry.disabled ? true : undefined}
                onClick={() => {
                  if (entry.disabled) return;
                  close(true);
                  entry.onSelect();
                }}
                className={itemClasses(entry.danger === true)}
              >
                {icon}
                <span className="min-w-0 flex-1">{entry.label}</span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
