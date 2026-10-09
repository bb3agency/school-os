"use client";

import { useCallback, useId, useRef, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { useDialogClose } from "@/lib/dialog-motion";
import { Button, type ButtonVariant } from "./Button";
import { DialogCloseButton } from "./DialogCloseButton";

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

  // Fades out before it closes (Escape too); instant under reduced motion.
  const close = useDialogClose(dialogRef, "modal");

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
          "dialog-motion m-auto w-[min(32rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-0 text-ink shadow-popover",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 p-4 pb-2 sm:p-6 sm:pb-2">
          <div className="min-w-0 space-y-1">
            <h2 id={titleId} className="text-lg font-semibold">
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
        {children ? <div className="space-y-4 px-4 py-4 sm:px-6">{children}</div> : null}
        {footer ? (
          <div className="dialog-footer flex flex-wrap justify-end gap-2 rounded-b-xl border-t border-border bg-surface-muted px-4 py-3 max-sm:[&>*]:flex-1 sm:px-6 sm:py-4">
            {footer}
          </div>
        ) : null}
      </dialog>
    </>
  );
}
