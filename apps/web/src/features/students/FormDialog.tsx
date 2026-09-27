"use client";

import type { QueryKey } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useCallback, useId, useRef, useState, type ReactNode } from "react";
import type { z } from "zod";
import { Button, type ButtonSize, type ButtonVariant } from "@/components/ui/Button";
import { useApiForm, type FieldErrors } from "@/lib/forms";
import { ProblemAlert, type ProblemNamespace } from "./ProblemAlert";

export interface FormDialogProps<TSchema extends z.ZodType, TResult> {
  triggerLabel: ReactNode;
  triggerVariant?: ButtonVariant;
  triggerSize?: ButtonSize;
  /** Extra accessible description for the trigger (which row it acts on). */
  triggerDescription?: string;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel: ReactNode;
  confirmVariant?: ButtonVariant;
  schema: TSchema;
  submit: (data: z.output<TSchema>, idempotencyKey: string) => Promise<TResult>;
  invalidate?: readonly QueryKey[];
  extra?: (form: HTMLFormElement) => Record<string, unknown>;
  fieldMap?: (serverField: string) => string | undefined;
  children?: (errors: FieldErrors) => ReactNode;
  onSuccess?: (result: TResult) => void;
  /** Called when the dialog opens (reset per-open state held by the caller). */
  onOpen?: () => void;
  /** Feature messages for API codes (e.g. `identity_change_required`). */
  problems: ProblemNamespace;
  /** Extra help under a failure, e.g. a link to the change-request workflow. */
  problemAction?: (error: unknown) => ReactNode;
}

/**
 * Confirm-and-submit dialog on the native <dialog> (focus trap, Escape closes, focus returns to
 * the trigger), like the shared ActionDialog, but failures are explained with this feature's
 * messages (ProblemAlert) so a refusal such as `identity_change_required` says what to do
 * instead of a generic "no permission".
 */
export function FormDialog<TSchema extends z.ZodType, TResult>({
  triggerLabel,
  triggerVariant = "secondary",
  triggerSize = "md",
  triggerDescription,
  title,
  description,
  confirmLabel,
  confirmVariant = "primary",
  schema,
  submit,
  invalidate,
  extra,
  fieldMap,
  children,
  onSuccess,
  onOpen,
  problems,
  problemAction,
}: FormDialogProps<TSchema, TResult>) {
  const tc = useTranslations("common");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const hintId = useId();
  const [open, setOpen] = useState(false);
  const close = useCallback(() => dialogRef.current?.close(), []);

  const form = useApiForm({
    schema,
    submit,
    ...(invalidate ? { invalidate } : {}),
    ...(extra ? { extra } : {}),
    ...(fieldMap ? { fieldMap } : {}),
    onSuccess: (result) => {
      onSuccess?.(result);
      close();
    },
  });

  const show = () => {
    form.reset();
    onOpen?.();
    setOpen(true);
    dialogRef.current?.showModal();
  };

  return (
    <>
      <Button
        ref={triggerRef}
        variant={triggerVariant}
        size={triggerSize}
        onClick={show}
        aria-haspopup="dialog"
        aria-describedby={triggerDescription ? hintId : undefined}
      >
        {triggerLabel}
      </Button>
      {triggerDescription ? (
        <span id={hintId} className="sr-only">
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
        className="m-auto w-[min(36rem,calc(100vw-2rem))] rounded-lg border border-border bg-surface p-0 text-ink shadow-xl"
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
                aria-label={tc("close")}
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
            <form noValidate onSubmit={form.onSubmit}>
              <div className="max-h-[60vh] space-y-4 overflow-y-auto p-5">
                {children?.(form.errors)}
                <ProblemAlert
                  error={form.error}
                  namespace={problems}
                  action={form.error !== undefined ? problemAction?.(form.error) : undefined}
                />
              </div>
              <div className="flex flex-wrap justify-end gap-2 border-t border-border p-5">
                <Button variant="secondary" onClick={close}>
                  {tc("cancel")}
                </Button>
                <Button
                  type="submit"
                  variant={confirmVariant}
                  disabled={form.pending}
                  aria-disabled={form.pending || undefined}
                >
                  {form.pending ? tc("working") : confirmLabel}
                </Button>
              </div>
            </form>
          </>
        ) : null}
      </dialog>
    </>
  );
}
