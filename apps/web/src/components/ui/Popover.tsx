"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface PopoverProps {
  /** The trigger's content (text, or an icon with `label`). */
  trigger: ReactNode;
  /** Accessible name of the trigger when it shows only an icon. */
  label?: string;
  /** Names the panel (a non-modal dialog). */
  title: string;
  children: ReactNode;
  align?: "start" | "end";
  className?: string;
  triggerClassName?: string;
  panelClassName?: string;
}

/**
 * A small non-modal panel under its button (column visibility, filter choices, help).
 * Disclosure pattern: the button says whether it is open (`aria-expanded`); the panel is a
 * labelled `role="dialog"` (not modal: the page stays usable). Escape closes and returns
 * focus to the button; a click outside or focus leaving closes it (WCAG 1.4.13). The panel
 * exists only after a user action, so its CSS entrance never plays on page load. Native
 * Popover API is not used: it is not Baseline widely available until mid-2027.
 */
export function Popover({
  trigger,
  label,
  title,
  children,
  align = "start",
  className,
  triggerClassName,
  panelClassName,
}: PopoverProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    // Focus leaving the button and the panel closes it.
    const root = rootRef.current;
    const onFocusOut = (event: FocusEvent) => {
      const next = event.relatedTarget as Node | null;
      if (next && !root?.contains(next)) setOpen(false);
    };
    root?.addEventListener("focusout", onFocusOut);
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      root?.removeEventListener("focusout", onFocusOut);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className={cn("relative inline-flex", className)}>
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={label}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "pressable inline-flex min-h-10 items-center gap-2 rounded-md border border-border-soft bg-surface px-3 text-sm font-semibold text-ink hover:bg-surface-muted pointer-coarse:min-h-11",
          triggerClassName,
        )}
      >
        {trigger}
      </button>
      {open ? (
        <div
          ref={panelRef}
          id={panelId}
          role="dialog"
          aria-label={title}
          tabIndex={-1}
          className={cn(
            "overlay-panel menu-in absolute top-full z-40 mt-1 w-72 max-w-[calc(100vw-2rem)] p-3 focus:outline-none",
            align === "end" ? "end-0 origin-top-right" : "start-0 origin-top-left",
            panelClassName,
          )}
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}
