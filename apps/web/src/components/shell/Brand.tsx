import { useTranslations } from "next-intl";
import { cn } from "@/lib/cn";

export type BrandTone = "school" | "platform";

/**
 * The SchoolOS mark (docs/17 §3.5): a small rounded square in the accent (ink blue), with a
 * white ledger line across it; amber on the operator console's dark chrome. Inline SVG with
 * presentation classes only (CSP-safe), decorative: the wordmark text or the link around it
 * carries the name.
 */
export function BrandMark({
  tone = "school",
  className,
}: {
  tone?: BrandTone;
  className?: string | undefined;
}) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      className={cn("size-6 shrink-0", className)}
      data-brand-mark=""
    >
      <rect
        x="1"
        y="1"
        width="22"
        height="22"
        rx="6"
        className={tone === "platform" ? "fill-platform-accent" : "fill-accent"}
      />
      <path
        d="M7 9.5h10M7 14.5h6"
        strokeWidth="2.25"
        strokeLinecap="round"
        fill="none"
        className={tone === "platform" ? "stroke-platform-accent-ink" : "stroke-on-accent"}
      />
    </svg>
  );
}

/**
 * Mark + "SchoolOS" in PP Mori semibold. The visible text is the accessible name; put it in a
 * link home where it should navigate. Sizes: `md` (sidebar, top bars), `lg` (sign-in cards).
 */
export function Wordmark({
  tone = "school",
  size = "md",
  className,
  textClassName,
}: {
  tone?: BrandTone;
  size?: "md" | "lg";
  className?: string;
  /** Hide the text visually (compact sidebar: `collapsed:sr-only`); it stays the name. */
  textClassName?: string;
}) {
  const t = useTranslations("common");
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-2", className)}>
      <BrandMark tone={tone} className={size === "lg" ? "size-8" : undefined} />
      <span
        className={cn(
          "min-w-0 font-semibold",
          size === "lg" ? "text-xl" : "text-base",
          tone === "platform" ? "text-platform-ink" : "text-ink",
          textClassName,
        )}
      >
        {t("appName")}
      </span>
    </span>
  );
}
