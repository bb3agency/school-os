import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Field, controlClasses } from "./Input";

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps extends Omit<ComponentProps<"select">, "children"> {
  options: readonly SelectOption[];
  /** Shown as a first, empty option ("Choose a plan"). */
  placeholder?: string;
}

/**
 * Native <select> (keyboard, screen-reader and mobile friendly without extra code), styled
 * as a filled control with a chevron drawn by CSS (`.select-chevron`, no extra markup).
 */
export function Select({ options, placeholder, className, ...props }: SelectProps) {
  return (
    <select
      className={cn(
        controlClasses,
        "select-chevron min-h-10 pr-10 pointer-coarse:min-h-11",
        className,
      )}
      {...props}
    >
      {placeholder !== undefined ? <option value="">{placeholder}</option> : null}
      {options.map((option) => (
        <option key={option.value} value={option.value} disabled={option.disabled}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export interface SelectFieldProps extends Omit<SelectProps, "id"> {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  id?: string | undefined;
}

export function SelectField({ label, hint, error, id, className, ...props }: SelectFieldProps) {
  return (
    <Field label={label} hint={hint} error={error} id={id} className={className}>
      {({ id: controlId, describedBy, invalid }) => (
        <Select
          id={controlId}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          {...props}
        />
      )}
    </Field>
  );
}
