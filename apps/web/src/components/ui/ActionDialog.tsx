"use client";

import type { QueryKey } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useCallback, useId, useRef, useState, type ReactNode } from "react";
import type { z } from "zod";
import { cn } from "@/lib/cn";
import { useApiForm, type FieldErrors } from "@/lib/forms";
import { Alert } from "./Alert";
import { ApiErrorAlert } from "./ApiErrorAlert";
import { Button, type ButtonSize, type ButtonVariant } from "./Button";

export interface ActionDialogProps<TSchema extends z.ZodType, TResult> {
  /** Text of the button that opens the dialog. */
  triggerLabel: ReactNode;
  triggerVariant?: ButtonVariant;
  triggerSize?: ButtonSize;
  triggerDisabled?: boolean;
  /** Extra accessible description for the trigger (e.g. which row it acts on). */
  triggerDescription?: string;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel: ReactNode;
  confirmVariant?: ButtonVariant;
  /** Risky actions: say up front that the server may ask for MFA again (ADR-0018). */
  stepUp?: boolean;
  /** Two-person actions: explain that a different operator must do the second step. */
  note?: ReactNode;
  schema: TSchema;
  submit: (data: z.output<TSchema>, idempotencyKey: string) => Promise<TResult>;
  fieldMap?: (serverField: string) => string | undefined;
  invalidate?: readonly QueryKey[];
  extra?: (form: HTMLFormElement) => Record<string, unknown>;
  /** Form fields; receives translated field errors by name. */
  children?: (errors: FieldErrors) => ReactNode;
  /** Shown instead of the form after success (e.g. a one-time secret). Otherwise it closes. */
  renderResult?: (result: TResult, close: () => void) => ReactNode;
  onSuccess?: (result: TResult) => void;
  className?: string;
}

/**
 * Confirm-and-submit dialog on the native <dialog> (focus trap, Escape to close, focus
 * back to the trigger). Every field has a label; errors are linked with aria-describedby;
 * the first invalid field receives focus. Keyboard only: Tab to the trigger, Enter, fill
 * the fields, Enter to confirm or Escape to cancel.
 */
export function ActionDialog<TSchema extends z.ZodType, TResult>({
  triggerLabel,
  triggerVariant = "secondary",
  triggerSize = "md",
  triggerDisabled = false,
  triggerDescription,
  title,
  description,
  confirmLabel,
  confirmVariant = "primary",
  stepUp = false,
  note,
  schema,
  submit,
  fieldMap,
  invalidate,
  extra,
  children,
  renderResult,
  onSuccess,
  className,
}: ActionDialogProps<TSchema, TResult>) {
  const t = useTranslations("common");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const triggerHintId = useId();
  const [open, setOpen] = useState(false);

  const close = useCallback(() => dialogRef.current?.close(), []);

  const form = useApiForm({
    schema,
    submit,
    ...(fieldMap ? { fieldMap } : {}),
    ...(invalidate ? { invalidate } : {}),
    ...(extra ? { extra } : {}),
    onSuccess: (result) => {
      onSuccess?.(result);
      if (!renderResult) close();
    },
  });

  const show = useCallback(() => {
    form.reset();
    setOpen(true);
    dialogRef.current?.showModal();
  }, [form]);

  const showingResult = renderResult !== undefined && form.result !== undefined;

  return (
    <>
      <Button
        ref={triggerRef}
        variant={triggerVariant}
        size={triggerSize}
        onClick={show}
        disabled={triggerDisabled}
        aria-haspopup="dialog"
        aria-describedby={triggerDescription ? triggerHintId : undefined}
      >
        {triggerLabel}
      </Button>
      {triggerDescription ? (
        <span id={triggerHintId} className="sr-only">
          {triggerDescription}
        </span>
      ) : null}
      <dialog
        ref={dialogRef}
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        onClose={() => {
          setOpen(false);
          triggerRef.current?.focus();
        }}
        className={cn(
          "m-auto w-[min(36rem,calc(100vw-2rem))] rounded-lg border border-border bg-surface p-0 text-ink shadow-xl",
          className,
        )}
      >
        {open ? (
          <>
            <div className="flex items-start justify-between gap-4 border-b border-border p-5">
              <div className="space-y-1">
                <h2 id={titleId} className="text-lg font-semibold">
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
                aria-label={t("close")}
                className="rounded-md p-1 text-ink-muted hover:bg-surface-muted hover:text-ink"
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
            {showingResult && renderResult && form.result !== undefined ? (
              <div className="space-y-4 p-5">{renderResult(form.result, close)}</div>
            ) : (
              <form noValidate onSubmit={form.onSubmit}>
                <div className="max-h-[60vh] space-y-4 overflow-y-auto p-5">
                  {note ? <Alert tone="info">{note}</Alert> : null}
                  {children?.(form.errors)}
                  {stepUp ? <p className="text-sm text-ink-muted">{t("stepUpNote")}</p> : null}
                  <ApiErrorAlert error={form.error} />
                </div>
                <div className="flex flex-wrap justify-end gap-2 border-t border-border p-5">
                  <Button variant="secondary" onClick={close}>
                    {t("cancel")}
                  </Button>
                  <Button
                    type="submit"
                    variant={confirmVariant}
                    disabled={form.pending}
                    aria-disabled={form.pending || undefined}
                  >
                    {form.pending ? t("working") : confirmLabel}
                  </Button>
                </div>
              </form>
            )}
          </>
        ) : null}
      </dialog>
    </>
  );
}
