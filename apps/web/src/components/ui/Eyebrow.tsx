import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type EyebrowTone = "muted" | "ink" | "brand" | "inverse";

const tones: Record<EyebrowTone, string> = {
  muted: "text-ink-muted",
  ink: "text-ink",
  brand: "text-primary",
  inverse: "text-white",
};

/**
 * Mono uppercase label ("INTENT FLOW", "LINE 8 / 34") above a title or number.
 * In Telugu it switches to the sans with normal spacing (globals.css `.eyebrow:lang(te)`).
 */
export function Eyebrow({
  children,
  as: Tag = "p",
  tone = "muted",
  id,
  className,
}: {
  children: ReactNode;
  as?: "p" | "span" | "div" | "h2" | "h3";
  tone?: EyebrowTone;
  id?: string;
  className?: string;
}) {
  return (
    <Tag id={id} className={cn("eyebrow", tones[tone], className)}>
      {children}
    </Tag>
  );
}
