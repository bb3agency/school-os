"use client";

import type { QueryKey } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useCallback, useId, useRef, useState, type ReactNode } from "react";
import type { z } from "zod";
import { cn } from "@/lib/cn";
import { useDialogClose } from "@/lib/dialog-motion";
import { useApiForm, type FieldErrors } from "@/lib/forms";
import { Alert } from "./Alert";
import { ApiErrorAlert, type ErrorNamespace } from "./ApiErrorAlert";
import { Button, type ButtonSize, type ButtonVariant } from "./Button";
import { DialogCloseButton } from "./DialogCloseButton";

/** Renders a one-time result (e.g. a secret) with a way to close the dialog. */
function ResultSlot<TResult>({
  render,
  result,
  onDone,
}: {
  render: (result: TResult, close: () => void) => ReactNode;
  result: TResult;
  onDone: () => void;
}) {
  return <>{render(result, onDone)}</>;
}

export interface ActionDialogProps<TSchema extends z.ZodType, TResult> {
  /** Text of the button that opens the dialog. */
  triggerLabel: ReactNode;
  triggerVariant?: ButtonVariant;
  triggerSize?: ButtonSize;
  triggerDisabled?: boolean;
  /**
   * Hide the trigger but keep the dialog mounted, so a dialog that is open stays open (with its
   * error) when the thing it acts on becomes read-only behind it.
   */
  triggerHidden?: boolean;
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
  /**
   * What confirming changes, in one plain sentence, shown just above the buttons and read
   * as the confirm button's description (e.g. "Nothing on the student's record changes.").
   */
  consequence?: ReactNode;
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
  /** Feature messages for problem codes (`<namespace>.errors.<code>`), see ApiErrorAlert. */
  errorNamespace?: ErrorNamespace;
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
  triggerHidden = false,
  triggerDescription,
  title,
  description,
  confirmLabel,
  confirmVariant = "primary",
  stepUp = false,
  note,
  consequence,
  schema,
  submit,
  fieldMap,
  invalidate,
  extra,
  children,
  renderResult,
  onSuccess,
  className,
  errorNamespace,
}: ActionDialogProps<TSchema, TResult>) {
  const t = useTranslations("common");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const triggerHintId = useId();
  const consequenceId = useId();
  const [open, setOpen] = useState(false);

  // Fades out before it closes (Escape too); instant under reduced motion.
  const close = useDialogClose(dialogRef, "modal");

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

  const result = form.result;

  return (
    <>
      <Button
        ref={triggerRef}
        variant={triggerVariant}
        size={triggerSize}
        onClick={show}
        disabled={triggerDisabled}
        hidden={triggerHidden}
        aria-haspopup="dialog"
        aria-describedby={triggerDescription && !triggerHidden ? triggerHintId : undefined}
      >
        {triggerLabel}
      </Button>
      {triggerDescription && !triggerHidden ? (
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
          "dialog-motion m-auto w-[min(36rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-0 text-ink shadow-popover",
          className,
        )}
      >
        {open ? (
          <>
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
              <DialogCloseButton label={t("close")} onClick={close} />
            </div>
            {renderResult !== undefined && result !== undefined ? (
              <div className="space-y-4 px-4 py-4 sm:px-6">
                <ResultSlot render={renderResult} result={result} onDone={close} />
              </div>
            ) : (
              <form noValidate onSubmit={form.onSubmit}>
                <div className="max-h-[60vh] space-y-4 overflow-y-auto overscroll-contain px-4 py-4 max-sm:max-h-none sm:px-6">
                  {note ? <Alert tone="info">{note}</Alert> : null}
                  {children?.(form.errors)}
                  {stepUp ? <p className="text-sm text-ink-muted">{t("stepUpNote")}</p> : null}
                  <ApiErrorAlert error={form.error} namespace={errorNamespace} />
                </div>
                {/* The consequence and the buttons stay together at the bottom while the
                    body scrolls (docs/17 §5.7). */}
                <div className="dialog-footer rounded-b-xl border-t border-border bg-surface-muted">
                  {consequence ? (
                    <p
                      id={consequenceId}
                      className="px-4 pt-3 text-sm text-ink sm:px-6 sm:pt-4 sm:text-end"
                    >
                      {consequence}
                    </p>
                  ) : null}
                  <div
                    className={cn(
                      "flex flex-wrap justify-end gap-2 px-4 py-3 max-sm:pb-0 max-sm:[&>*]:flex-1 sm:px-6 sm:py-4",
                      consequence ? "pt-2 sm:pt-3" : undefined,
                    )}
                  >
                    <Button variant="secondary" onClick={close}>
                      {t("cancel")}
                    </Button>
                    <Button
                      type="submit"
                      variant={confirmVariant}
                      aria-describedby={consequence ? consequenceId : undefined}
                      disabled={form.pending}
                      aria-disabled={form.pending || undefined}
                      loading={form.pending}
                    >
                      {form.pending ? t("working") : confirmLabel}
                    </Button>
                  </div>
                </div>
              </form>
            )}
          </>
        ) : null}
      </dialog>
    </>
  );
}
