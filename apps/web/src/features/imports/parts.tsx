"use client";

import { useRef, useState, type DragEvent, type ReactNode } from "react";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Icon, type IconName } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

/**
 * Shared pieces of the data-in/out screens (imports, register photos, documents). Composed from
 * the primitives; nothing here changes what is sent to the API.
 */

/* ------------------------------------------------------------------ stepper */

export interface StepperProps {
  /** Accessible name of the list ("Import steps"). */
  label: string;
  steps: readonly string[];
  /** 0-based index of the current step; `steps.length` when every step is done. */
  current: number;
  /** "Step 2" above each name. */
  stepNumber: (number: number) => string;
  /** Read before a finished step's name ("Done:"). */
  doneLabel: string;
  /** The current step failed: its marker shows an alert, not a dot. */
  failed?: boolean;
  /** Read before a failed step's name ("Stopped:"). */
  failedLabel?: string;
}

/**
 * Horizontal step indicator (forms guidance: an ordered list, the current step marked with
 * `aria-current="step"`, finished steps say so in text). Colour is never the only signal:
 * a check, a number or an alert icon sits in each circle and the words are always there.
 */
export function Stepper({
  label,
  steps,
  current,
  stepNumber,
  doneLabel,
  failed = false,
  failedLabel,
}: StepperProps) {
  return (
    <ol aria-label={label} className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
      {steps.map((name, index) => {
        const state = index < current ? "done" : index === current ? "current" : "pending";
        const stopped = state === "current" && failed;
        return (
          <li
            key={name}
            aria-current={state === "current" ? "step" : undefined}
            className="flex min-w-0 items-center gap-3"
          >
            <span
              aria-hidden="true"
              className={cn(
                "flex size-8 shrink-0 items-center justify-center rounded-full border-2 text-sm font-medium",
                state === "done" && "border-success bg-success text-white",
                state === "current" && !stopped && "border-primary bg-primary-soft text-primary",
                stopped && "border-danger bg-danger-soft text-danger",
                state === "pending" && "border-border-control bg-surface text-ink-muted",
              )}
            >
              {state === "done" ? (
                <Icon name="check" className="size-4" />
              ) : stopped ? (
                <Icon name="alert" className="size-4" />
              ) : (
                index + 1
              )}
            </span>
            <span className="min-w-0">
              <Eyebrow as="span" className="block">
                {stepNumber(index + 1)}
              </Eyebrow>
              <span
                className={cn(
                  "block text-sm",
                  state === "pending" ? "text-ink-muted" : "font-medium text-ink",
                )}
              >
                {state === "done" ? <span className="sr-only">{doneLabel} </span> : null}
                {stopped && failedLabel ? <span className="sr-only">{failedLabel} </span> : null}
                {name}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/* ------------------------------------------------------------------ file drop zone */

/** The native file input's look inside a drop zone (a near-black "Choose file" button). */
export const fileInputClasses =
  "mx-auto block w-full max-w-md cursor-pointer rounded-md text-sm text-ink-muted " +
  "file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-action file:px-4 " +
  "file:py-2 file:font-medium file:text-on-action hover:file:bg-action-hover " +
  "disabled:cursor-not-allowed disabled:opacity-70";

/**
 * Dashed upload card around a real `<input type="file">` (it keeps its label, keyboard
 * behaviour and screen-reader name). Files dropped anywhere on the card are handed to that
 * input, exactly as if they had been chosen in the file chooser (only the first one when the
 * input takes a single file).
 */
export function FileDropZone({
  title,
  note,
  icon = "upload",
  disabled = false,
  children,
}: {
  /** "Choose a spreadsheet, or drag it here". */
  title: ReactNode;
  /** Short line under the input (accepted types). */
  note?: ReactNode;
  icon?: IconName;
  disabled?: boolean;
  /** The file input. */
  children: ReactNode;
}) {
  const zone = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);

  function input(): HTMLInputElement | null {
    return zone.current?.querySelector<HTMLInputElement>("input[type='file']") ?? null;
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    if (disabled || !event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setDragging(true);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    setDragging(false);
    const target = input();
    if (disabled || !target || event.dataTransfer.files.length === 0) return;
    event.preventDefault();
    const files = new DataTransfer();
    const dropped = [...event.dataTransfer.files];
    for (const file of target.multiple ? dropped : dropped.slice(0, 1)) files.items.add(file);
    target.files = files.files;
    target.dispatchEvent(new Event("change", { bubbles: true }));
    target.focus();
  }

  return (
    // Drop target only (pointer users); the file input inside stays the keyboard control.
    <div
      ref={zone}
      role="presentation"
      onDragOver={onDragOver}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={cn(
        "flex flex-col items-center gap-3 rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors",
        dragging ? "border-primary bg-primary-soft" : "border-border-control bg-surface-muted",
      )}
    >
      <span
        aria-hidden="true"
        className="flex size-12 items-center justify-center rounded-full bg-surface text-primary shadow-card"
      >
        <Icon name={icon} className="size-6" />
      </span>
      <p className="font-medium text-ink">{title}</p>
      {children}
      {note ? <p className="text-xs text-ink-muted">{note}</p> : null}
    </div>
  );
}
