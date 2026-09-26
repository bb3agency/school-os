import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger" | "platform";

const tones: Record<BadgeTone, string> = {
  neutral: "bg-surface-muted text-ink border-border",
  info: "bg-info-soft text-info-ink border-info-ink/30",
  success: "bg-success-soft text-success-ink border-success-ink/30",
  warning: "bg-warning-soft text-warning-ink border-warning-border/40",
  danger: "bg-danger-soft text-danger border-danger/30",
  platform: "bg-platform-accent text-platform-accent-ink border-platform-accent",
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
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
