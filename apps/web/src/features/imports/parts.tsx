"use client";

import { useRef, useState, type DragEvent, type ReactNode } from "react";
import { Icon, type IconName } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

/**
 * Shared pieces of the data-in/out screens (imports, register photos, documents). Composed from
 * the primitives; nothing here changes what is sent to the API.
 */

/* ------------------------------------------------------------------ file drop zone */

/** The native file input's look inside a drop zone (a near-black "Choose file" button). */
export const fileInputClasses =
  "mx-auto block w-full max-w-md cursor-pointer rounded-md text-sm text-ink-muted " +
  "file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-action file:px-4 " +
  "file:py-2 file:font-semibold file:text-on-action hover:file:bg-action-hover " +
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
      <p className="font-semibold text-ink">{title}</p>
      {children}
      {note ? <p className="text-xs text-ink-muted">{note}</p> : null}
    </div>
  );
}
