"use client";

import { useCallback, useId, useRef, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Button, type ButtonVariant } from "./Button";

export interface DialogProps {
  title: ReactNode;
  /** Text of the button that opens the dialog. */
  triggerLabel: ReactNode;
  triggerVariant?: ButtonVariant;
  /** Accessible name of the close button. */
  closeLabel: string;
  description?: ReactNode;
  children?: ReactNode;
  /** Footer actions. Buttons inside `<form method="dialog">` close the dialog natively. */
  footer?: ReactNode;
  className?: string;
}

/**
 * Modal built on the native <dialog> element (Baseline widely available): focus is
 * trapped and Escape closes it without extra code. Focus returns to the trigger on close.
 */
export function Dialog({
  title,
  triggerLabel,
  triggerVariant = "primary",
  closeLabel,
  description,
  children,
  footer,
  className,
}: DialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  const open = useCallback(() => {
    dialogRef.current?.showModal();
  }, []);

  const close = useCallback(() => {
    dialogRef.current?.close();
  }, []);

  return (
    <>
      <Button ref={triggerRef} variant={triggerVariant} onClick={open} aria-haspopup="dialog">
        {triggerLabel}
      </Button>
      <dialog
        ref={dialogRef}
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        onClose={() => triggerRef.current?.focus()}
        className={cn(
          "m-auto w-[min(32rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-0 text-ink shadow-popover",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 p-6 pb-2">
          <div className="space-y-1">
            <h2 id={titleId} className="text-lg font-medium">
              {title}
            </h2>
            {description ? (
              <p id={descriptionId} className="text-sm text-ink-muted">
                {description}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={close}
            aria-label={closeLabel}
            className="inline-flex size-9 shrink-0 items-center justify-center rounded-full border border-border-soft text-ink-muted hover:bg-surface-muted hover:text-ink"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              className="size-5"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>
        {children ? <div className="space-y-4 px-6 py-4">{children}</div> : null}
        {footer ? (
          <div className="flex flex-wrap justify-end gap-2 rounded-b-xl border-t border-border bg-surface-muted px-6 py-4">
            {footer}
          </div>
        ) : null}
      </dialog>
    </>
  );
}
