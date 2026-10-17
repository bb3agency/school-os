"use client";

import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useCallback, useRef, useState, type FormEvent } from "react";
import type { z } from "zod";
import { apiFieldErrors } from "@/lib/api-errors";
import { ApiError, newIdempotencyKey } from "@/lib/bff/query";
import { translateOr } from "@/lib/i18n-dynamic";

/**
 * Native <form> + zod + the BFF (PRD §8: instant inline validation that says how to fix it).
 *
 * - Client validation: zod issue messages are keys under `validation.*`.
 * - Server validation (422): each `errors[].field` (dotted body path) is mapped to a form
 *   field and its `message_key` translated under `errors.field.*`; unknown keys fall back to
 *   a generic "check this field" message. Anything not mapped shows in the form's alert.
 * - One Idempotency-Key per form "intent": a retry of the same values after a network error
 *   reuses it; a successful submit, or a retry with changed values, starts a new one (docs/09
 *   §2: the API refuses a key reused with another body, 422 `idempotency_key_reused`).
 * - A submit while the previous one is still running is ignored (double Enter or click).
 */

export type FieldErrors = Record<string, string>;

/** All string values of a form (files and repeated names are ignored). */
export function formValues(form: HTMLFormElement | FormData): Record<string, string> {
  const data = form instanceof FormData ? form : new FormData(form);
  const out: Record<string, string> = {};
  for (const [name, value] of data.entries()) {
    if (typeof value === "string" && !(name in out)) out[name] = value;
  }
  return out;
}

/** All values of a repeated field (checkbox groups, multi-selects). */
export function formList(form: HTMLFormElement | FormData, name: string): string[] {
  const data = form instanceof FormData ? form : new FormData(form);
  return data.getAll(name).filter((value): value is string => typeof value === "string");
}

/** zod issues → { field: validation key }. The first issue per field wins. */
export function zodErrorKeys(error: z.ZodError): Record<string, string> {
  const out: Record<string, string> = {};
  for (const issue of error.issues) {
    const field = issue.path.map(String).join(".") || "form";
    if (!(field in out)) out[field] = issue.message;
  }
  return out;
}

/** A stable text form of the parsed values; files count by name, size and date. */
function bodySignature(data: unknown): string {
  try {
    return JSON.stringify(data, (_key, value: unknown) =>
      typeof File !== "undefined" && value instanceof File
        ? { file: value.name, size: value.size, modified: value.lastModified }
        : value,
    );
  } catch {
    // Not serialisable: treat every attempt as new (a fresh key is always safe).
    return newIdempotencyKey();
  }
}

export interface ApiFormOptions<TSchema extends z.ZodType, TResult> {
  schema: TSchema;
  /** Extra values not held in named inputs (e.g. checkbox lists). */
  extra?: (form: HTMLFormElement) => Record<string, unknown>;
  submit: (data: z.output<TSchema>, idempotencyKey: string) => Promise<TResult>;
  /** Server field path → form field name (default: the last path segment). */
  fieldMap?: (serverField: string) => string | undefined;
  invalidate?: readonly QueryKey[];
  onSuccess?: (result: TResult, form: HTMLFormElement) => void;
  /** Fields that failed validation (client or server), e.g. to move a wizard to that step. */
  onInvalid?: (fields: string[]) => void;
}

export interface ApiFormState<TResult> {
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  errors: FieldErrors;
  /** Error for the form-level alert (not shown when every problem maps to a field). */
  error: unknown;
  pending: boolean;
  result: TResult | undefined;
  reset: () => void;
}

export function useApiForm<TSchema extends z.ZodType, TResult>(
  options: ApiFormOptions<TSchema, TResult>,
): ApiFormState<TResult> {
  const tv = useTranslations("validation");
  const tf = useTranslations("errors.field");
  const queryClient = useQueryClient();
  const [errors, setErrors] = useState<FieldErrors>({});
  const [error, setError] = useState<unknown>(undefined);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<TResult | undefined>(undefined);
  const keyRef = useRef<string>("");
  /** The values the current key was last sent with (a changed body needs a new key). */
  const sentRef = useRef<string | null>(null);
  /** Synchronous guard: React state is too late for a second Enter in the same frame. */
  const busyRef = useRef(false);

  const translateClient = useCallback((key: string) => translateOr(tv, key, "invalid"), [tv]);
  const translateServer = useCallback((key: string) => translateOr(tf, key, "invalid"), [tf]);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busyRef.current) return;
    const form = event.currentTarget;
    const opts = options;
    if (!keyRef.current) keyRef.current = newIdempotencyKey();
    const raw = { ...formValues(form), ...(opts.extra?.(form) ?? {}) };
    const parsed = opts.schema.safeParse(raw);
    if (!parsed.success) {
      const keys = zodErrorKeys(parsed.error);
      setErrors(Object.fromEntries(Object.entries(keys).map(([f, k]) => [f, translateClient(k)])));
      setError(undefined);
      opts.onInvalid?.(Object.keys(keys));
      const first = form.querySelector<HTMLElement>("[aria-invalid='true']");
      requestAnimationFrame(() =>
        (form.querySelector<HTMLElement>("[aria-invalid='true']") ?? first)?.focus(),
      );
      return;
    }
    setErrors({});
    setError(undefined);
    setPending(true);
    busyRef.current = true;
    const body = bodySignature(parsed.data);
    if (sentRef.current !== null && sentRef.current !== body) {
      keyRef.current = newIdempotencyKey();
    }
    sentRef.current = body;
    opts
      .submit(parsed.data, keyRef.current)
      .then(async (value) => {
        keyRef.current = newIdempotencyKey();
        sentRef.current = null;
        setResult(value);
        await Promise.all(
          (opts.invalidate ?? []).map((queryKey) => queryClient.invalidateQueries({ queryKey })),
        );
        opts.onSuccess?.(value, form);
      })
      .catch((failure: unknown) => {
        const fields = apiFieldErrors(failure);
        const mapped: FieldErrors = {};
        let unmapped = fields.length === 0;
        for (const { field, key } of fields) {
          const name = opts.fieldMap ? opts.fieldMap(field) : field.split(".").pop();
          if (name && form.elements.namedItem(name)) {
            if (!(name in mapped)) mapped[name] = translateServer(key);
          } else {
            unmapped = true;
          }
        }
        setErrors(mapped);
        if (Object.keys(mapped).length > 0) opts.onInvalid?.(Object.keys(mapped));
        // A 4xx other than validation always means "don't retry with the same key".
        if (failure instanceof ApiError && failure.status < 500) {
          keyRef.current = newIdempotencyKey();
          sentRef.current = null;
        }
        setError(unmapped || !(failure instanceof ApiError) ? failure : undefined);
      })
      .finally(() => {
        busyRef.current = false;
        setPending(false);
      });
  };

  const reset = useCallback(() => {
    setErrors({});
    setError(undefined);
    setResult(undefined);
    keyRef.current = newIdempotencyKey();
    sentRef.current = null;
  }, []);

  return { onSubmit, errors, error, pending, result, reset };
}
