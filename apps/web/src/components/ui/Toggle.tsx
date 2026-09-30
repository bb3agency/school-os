"use client";

import { useId, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface ToggleProps {
  /** Visible label next to the switch (its accessible name). */
  label: ReactNode;
  /** Optional hint under the label (becomes the accessible description). */
  description?: ReactNode;
  /** Controlled state. */
  checked?: boolean;
  /** Uncontrolled starting state. */
  defaultChecked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
  disabled?: boolean;
  /** When set, a hidden input submits "on"/"off" with the surrounding form. */
  name?: string;
  id?: string;
  /** Put the label before the switch (settings rows) instead of after it. */
  labelFirst?: boolean;
  className?: string;
}

/**
 * On/off switch: a `<button role="switch" aria-checked>` (Space and Enter toggle it),
 * green track when on. The label text is always visible; colour is not the only signal
 * (the knob moves too). For choices that are submitted with a form, prefer a checkbox
 * unless the setting applies immediately; `name` adds a hidden input for plain forms.
 */
export function Toggle({
  label,
  description,
  checked,
  defaultChecked = false,
  onCheckedChange,
  disabled = false,
  name,
  id,
  labelFirst = false,
  className,
}: ToggleProps) {
  const generated = useId();
  const switchId = id ?? `switch-${generated}`;
  const labelId = `${switchId}-label`;
  const descriptionId = description ? `${switchId}-description` : undefined;
  const [internal, setInternal] = useState(defaultChecked);
  const on = checked ?? internal;

  function toggle() {
    const next = !on;
    if (checked === undefined) setInternal(next);
    onCheckedChange?.(next);
  }

  const control = (
    <button
      type="button"
      role="switch"
      id={switchId}
      aria-checked={on}
      aria-labelledby={labelId}
      aria-describedby={descriptionId}
      disabled={disabled}
      onClick={toggle}
      className={cn(
        "relative mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-60",
        on ? "border-success bg-success" : "border-border-control bg-border-control",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "inline-block size-5 rounded-full bg-white shadow-raised transition-transform",
          on ? "translate-x-5" : "translate-x-0.5",
        )}
      />
    </button>
  );

  const text = (
    <div className="min-w-0">
      {/* A <button> is labelable: clicking the text toggles it, like a checkbox label. */}
      <label
        id={labelId}
        htmlFor={switchId}
        className={cn("block text-sm font-semibold text-ink", !disabled && "cursor-pointer")}
      >
        {label}
      </label>
      {description ? (
        <span id={descriptionId} className="block text-sm text-ink-muted">
          {description}
        </span>
      ) : null}
    </div>
  );

  return (
    <div className={cn("flex items-start gap-3", labelFirst && "justify-between", className)}>
      {labelFirst ? text : control}
      {labelFirst ? control : text}
      {name ? <input type="hidden" name={name} value={on ? "on" : "off"} /> : null}
    </div>
  );
}
