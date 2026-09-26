import { useId, type ComponentProps, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Label } from "./Label";

export const controlClasses =
  "block w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-base text-ink " +
  "placeholder:text-ink-muted aria-invalid:border-danger aria-invalid:border-2 " +
  "disabled:cursor-not-allowed disabled:bg-surface-muted";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(controlClasses, "min-h-10", className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn(controlClasses, "min-h-24", className)} {...props} />;
}

export interface FieldIds {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
}

export interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  /** Fixed id (e.g. for tests or error-summary links); generated when omitted. */
  id?: string | undefined;
  className?: string | undefined;
  children: (ids: FieldIds) => ReactNode;
}

/**
 * Label + control + hint + error, with the ids wired for assistive tech:
 * `<label for>`, `aria-describedby` (hint, error) and `aria-invalid`.
 */
export function Field({ label, hint, error, id, className, children }: FieldProps) {
  const generated = useId();
  const controlId = id ?? `field-${generated}`;
  const hintId = hint ? `${controlId}-hint` : undefined;
  const errorId = error ? `${controlId}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("space-y-1", className)}>
      <Label htmlFor={controlId}>{label}</Label>
      {hint ? (
        <p id={hintId} className="text-sm text-ink-muted">
          {hint}
        </p>
      ) : null}
      {children({ id: controlId, describedBy, invalid: Boolean(error) })}
      {error ? (
        <p id={errorId} className="text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export interface TextFieldProps extends Omit<ComponentProps<"input">, "id" | "children"> {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  id?: string | undefined;
}

export function TextField({ label, hint, error, id, className, ...inputProps }: TextFieldProps) {
  return (
    <Field label={label} hint={hint} error={error} id={id} className={className}>
      {({ id: controlId, describedBy, invalid }) => (
        <Input
          id={controlId}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          {...inputProps}
        />
      )}
    </Field>
  );
}

export interface TextAreaFieldProps extends Omit<ComponentProps<"textarea">, "id" | "children"> {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  id?: string | undefined;
}

export function TextAreaField({ label, hint, error, id, className, ...props }: TextAreaFieldProps) {
  return (
    <Field label={label} hint={hint} error={error} id={id} className={className}>
      {({ id: controlId, describedBy, invalid }) => (
        <Textarea
          id={controlId}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          {...props}
        />
      )}
    </Field>
  );
}
