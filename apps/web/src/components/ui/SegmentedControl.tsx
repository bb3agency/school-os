"use client";

import { useId, useState, type ChangeEvent, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface SegmentOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

export interface SegmentedControlProps {
  /** Group name read by screen readers ("Show messages from"); visually hidden by default. */
  legend: ReactNode;
  legendVisible?: boolean;
  options: readonly SegmentOption[];
  /** Controlled value. */
  value?: string;
  /** Uncontrolled starting value (defaults to the first option). */
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  /** Form field name; generated when omitted. The checked value submits with a form. */
  name?: string;
  size?: "sm" | "md";
  /** Stretch the segments to fill the width. */
  block?: boolean;
  className?: string;
}

/**
 * One choice among 2–5 options shown side by side ("Both speakers | Customer | Agent"):
 * a `<fieldset>` of native radio buttons, so arrow keys, Tab order, form submission and
 * screen-reader announcements work without custom code. The checked segment is the
 * white raised one (`:has(:checked)`; Baseline widely available). For switching panels
 * of content use `Tabs`; for switching pages use `TabNav`.
 */
export function SegmentedControl({
  legend,
  legendVisible = false,
  options,
  value,
  defaultValue,
  onValueChange,
  name,
  size = "md",
  block = false,
  className,
}: SegmentedControlProps) {
  const generated = useId();
  const groupName = name ?? `segment-${generated}`;
  const [internal, setInternal] = useState(defaultValue ?? options[0]?.value ?? "");
  const current = value ?? internal;

  function onChange(event: ChangeEvent<HTMLInputElement>) {
    if (value === undefined) setInternal(event.target.value);
    onValueChange?.(event.target.value);
  }

  return (
    <fieldset className={cn("min-w-0", className)}>
      <legend className={legendVisible ? "mb-1 text-sm font-semibold text-ink" : "sr-only"}>
        {legend}
      </legend>
      <div
        className={cn(
          "max-w-full flex-wrap gap-1 rounded-lg bg-surface-sunken p-1",
          block ? "flex w-full" : "inline-flex",
        )}
      >
        {options.map((option) => (
          <label
            key={option.value}
            className={cn(
              "relative inline-flex cursor-pointer items-center justify-center rounded-md border border-transparent text-center font-semibold text-ink-muted transition-colors",
              "hover:text-ink",
              "has-checked:border-border has-checked:bg-surface has-checked:text-ink has-checked:shadow-raised",
              "has-focus-visible:outline-3 has-focus-visible:outline-offset-2 has-focus-visible:outline-focus",
              "has-disabled:cursor-not-allowed has-disabled:opacity-60",
              size === "sm" ? "min-h-8 px-3 text-xs" : "min-h-9 px-4 text-sm",
              block && "flex-1",
            )}
          >
            <input
              type="radio"
              name={groupName}
              value={option.value}
              checked={current === option.value}
              disabled={option.disabled}
              onChange={onChange}
              className="sr-only"
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
