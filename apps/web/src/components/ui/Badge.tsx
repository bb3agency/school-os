import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Icon } from "./Icon";

export type BadgeTone =
  "neutral" | "info" | "success" | "warning" | "danger" | "platform" | "violet" | "teal";

const tones: Record<BadgeTone, string> = {
  neutral: "bg-surface-muted text-ink border-border",
  info: "bg-info-soft text-info-ink border-info-border",
  success: "bg-success-soft text-success-ink border-success-ink/30",
  warning: "bg-warning-soft text-warning-ink border-warning-border/40",
  danger: "bg-danger-soft text-danger border-danger/30",
  platform: "bg-platform-accent text-platform-accent-ink border-platform-accent",
  violet: "bg-violet-soft text-violet-ink border-violet-ink/25",
  teal: "bg-teal-soft text-teal-ink border-teal-ink/25",
};

/** Status label. Colour is never the only signal: the text always says the status. */
export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * Pill variants from the reference look:
 * - `progress` / `review` / `done`: gradient status pills, white text (≥ 5.2:1 at the light stop)
 * - `dark`: near-black pill (delta badges, counters)
 * - `date`: outlined light-blue chip ("18 Jun")
 * - `tag`: soft grey chip · `sample`: neutral "Sample data" chip
 * - `positive` / `negative`: pale green / pale red with a matching border
 * - `command`: mono slash-command chip ("/summarise")
 */
export type PillVariant =
  | "progress"
  | "review"
  | "done"
  | "dark"
  | "date"
  | "tag"
  | "sample"
  | "positive"
  | "negative"
  | "command";

const pills: Record<PillVariant, string> = {
  progress: "pill-gradient-blue border-transparent text-white",
  review: "pill-gradient-violet border-transparent text-white",
  done: "pill-gradient-teal border-transparent text-white",
  dark: "border-action bg-action text-on-action",
  date: "border-info-border bg-info-soft text-primary",
  tag: "border-border bg-surface-muted text-ink-muted",
  sample: "border-border-soft bg-surface text-ink-muted",
  positive: "border-positive-border bg-positive-soft text-positive-ink",
  negative: "border-danger/30 bg-danger-soft text-danger",
  command: "border-border bg-surface-muted text-ink",
};

export function Pill({
  variant = "tag",
  size = "sm",
  children,
  className,
}: {
  variant?: PillVariant;
  /** `sm` (default) 12px text; `md` 14px text for hero or header chips. */
  size?: "sm" | "md";
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border font-medium",
        // md chips carry sentences (hero, headers): they may wrap on phones.
        size === "md" ? "px-3 py-1 text-sm" : "px-2.5 py-0.5 text-xs whitespace-nowrap",
        variant === "command" ? "font-mono" : "font-sans",
        pills[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}

export type DeltaDirection = "up" | "down" | "flat";

/**
 * Dark delta badge ("+2.7%"). The arrow is decorative; `label` (e.g. "up 2.7% on last
 * month") is read instead of the bare number when given.
 */
export function DeltaPill({
  value,
  direction = "flat",
  label,
  className,
}: {
  value: string;
  direction?: DeltaDirection;
  label?: string;
  className?: string;
}) {
  return (
    <Pill variant="dark" className={cn("tabular-nums", className)}>
      {direction === "flat" ? null : (
        <Icon name={direction === "up" ? "arrowUp" : "arrowDown"} className="size-3" />
      )}
      {label ? (
        <>
          <span aria-hidden="true">{value}</span>
          <span className="sr-only">{label}</span>
        </>
      ) : (
        value
      )}
    </Pill>
  );
}
