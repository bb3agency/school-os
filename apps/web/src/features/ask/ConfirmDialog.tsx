"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, type FormEvent, type ReactNode } from "react";
import { Button, type ButtonVariant } from "@/components/ui/Button";

/**
 * A controlled confirm dialog on the native modal `<dialog>` (focus moves inside and is
 * trapped, Escape closes, focus returns to what opened it). Used for delete, forget
 * everything and rename (with a field as `children`). Opens with a short pop (docs/17 §5.3).
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  confirmVariant = "danger",
  onConfirm,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  body?: ReactNode;
  confirmLabel: string;
  confirmVariant?: ButtonVariant;
  /** Return false to keep the dialog open (e.g. a field is invalid). */
  onConfirm: (form: HTMLFormElement) => boolean | void;
  onClose: () => void;
  children?: ReactNode;
}) {
  const t = useTranslations("common");
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const bodyId = useId();
  const returnTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      returnTo.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={body ? bodyId : undefined}
      onClose={() => {
        onClose();
        const target = returnTo.current;
        if (target?.isConnected) target.focus();
      }}
      className="chat-dialog m-auto w-[min(28rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-0 text-ink shadow-popover"
    >
      {open ? (
        <form
          noValidate
          onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            if (onConfirm(event.currentTarget) !== false) ref.current?.close();
          }}
        >
          <div className="space-y-2 p-4 sm:p-6">
            <h2 id={titleId} className="text-lg font-medium">
              {title}
            </h2>
            {body ? (
              <div id={bodyId} className="text-sm text-ink-muted">
                {body}
              </div>
            ) : null}
            {children}
          </div>
          <div className="flex flex-wrap justify-end gap-2 rounded-b-xl border-t border-border bg-surface-muted px-4 py-3 max-sm:[&>*]:flex-1 sm:px-6 sm:py-4">
            <Button variant="secondary" onClick={() => ref.current?.close()}>
              {t("cancel")}
            </Button>
            <Button type="submit" variant={confirmVariant}>
              {confirmLabel}
            </Button>
          </div>
        </form>
      ) : null}
    </dialog>
  );
}
