import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "./Icon";

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
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * Pill variants (docs/17 §4). Solid, calm chips: a soft tint, strong text and a hairline, so
 * colour only repeats what the text says (WCAG 1.4.1) and every pair is AA (tokens.test.ts).
 * - `progress` / `review` / `done`: workflow states (blue / violet / teal tints) with a small
 *   decorative icon (clock / eye / check); pass `icon={null}` to leave it out
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
  progress: "border-info-border bg-info-soft text-info-ink",
  review: "border-violet-ink/25 bg-violet-soft text-violet-ink",
  done: "border-teal-ink/25 bg-teal-soft text-teal-ink",
  dark: "border-action bg-action text-on-action",
  date: "border-info-border bg-info-soft text-primary",
  tag: "border-border bg-surface-muted text-ink-muted",
  sample: "border-border-soft bg-surface text-ink-muted",
  positive: "border-positive-border bg-positive-soft text-positive-ink",
  negative: "border-danger/30 bg-danger-soft text-danger",
  command: "border-border bg-surface-muted text-ink",
};

/** The workflow states carry an icon by default, so the state never rests on colour alone. */
const STATUS_ICONS: Partial<Record<PillVariant, IconName>> = {
  progress: "clock",
  review: "eye",
  done: "check",
};

export function Pill({
  variant = "tag",
  size = "sm",
  icon,
  children,
  className,
}: {
  variant?: PillVariant;
  /** `sm` (default) 12px text; `md` 14px text for hero or header chips. */
  size?: "sm" | "md";
  /** Leading decorative icon. Status variants have one by default; `null` removes it. */
  icon?: IconName | null;
  children: ReactNode;
  className?: string;
}) {
  const iconName = icon === undefined ? STATUS_ICONS[variant] : icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border font-semibold",
        // md chips carry sentences (hero, headers): they may wrap on phones.
        size === "md" ? "px-3 py-1 text-sm" : "px-2.5 py-0.5 text-xs whitespace-nowrap",
        variant === "command" ? "font-mono" : "font-sans",
        pills[variant],
        className,
      )}
    >
      {iconName ? (
        <Icon name={iconName} className={cn("shrink-0", size === "md" ? "size-4" : "size-3")} />
      ) : null}
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
