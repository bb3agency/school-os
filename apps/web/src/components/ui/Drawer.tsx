"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { useDialogClose } from "@/lib/dialog-motion";
import { DialogCloseButton } from "./DialogCloseButton";

export interface DrawerProps {
  open: boolean;
  /** Called after the drawer closed (Escape, the close button, the backdrop or `open`). */
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  /** Accessible name of the close button ("Close"). */
  closeLabel: string;
  children: ReactNode;
  /** Sticky footer (Save / Cancel). */
  footer?: ReactNode;
  className?: string;
}

/**
 * Side sheet from the end edge for a record's details or a filter panel (docs/17 §4.2), on
 * the native modal `<dialog>`: focus moves in and stays, the page behind is inert and does
 * not scroll, Escape and the backdrop close it, focus returns to whatever opened it. On phones it takes the full
 * width. Controlled by `open`.
 */
export function Drawer({
  open,
  onClose,
  title,
  description,
  closeLabel,
  children,
  footer,
  className,
}: DrawerProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(null);
  const titleId = useId();
  const descriptionId = useId();
  const close = useDialogClose(ref, "modal");

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      opener.current = document.activeElement;
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    } else if (!open && dialog.open) {
      close();
    }
  }, [open, close]);

  // A click on the dimmed backdrop lands on the <dialog> box itself (the sheet fills the rest).
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const onClick = (event: MouseEvent) => {
      if (event.target === dialog) close();
    };
    dialog.addEventListener("click", onClick);
    return () => dialog.removeEventListener("click", onClick);
  }, [close]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      onClose={() => {
        const target = opener.current;
        if (target instanceof HTMLElement && target.isConnected) target.focus();
        onClose();
      }}
      className={cn("drawer drawer-end", className)}
      data-print="hide"
    >
      <div className="drawer-sheet">
        <div className="flex items-start justify-between gap-4 border-b border-border px-4 py-3 sm:px-5">
          <div className="min-w-0 space-y-1">
            <h2 id={titleId} className="text-lg font-semibold break-anywhere">
              {title}
            </h2>
            {description ? (
              <p id={descriptionId} className="text-sm text-ink-muted">
                {description}
              </p>
            ) : null}
          </div>
          <DialogCloseButton label={closeLabel} onClick={close} />
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-4 py-4 sm:px-5">
          {open ? children : null}
        </div>
        {footer ? (
          <div className="dialog-footer flex flex-wrap justify-end gap-2 border-t border-border bg-surface-muted px-4 py-3 sm:px-5">
            {footer}
          </div>
        ) : null}
      </div>
    </dialog>
  );
}
