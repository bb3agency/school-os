import { cn } from "@/lib/cn";

/**
 * The round "S" mark (decorative: the wordmark or the link around it carries the name).
 * Near-black in the school app, yellow on the platform's dark chrome.
 */
export function BrandMark({
  tone = "school",
  className,
}: {
  tone?: "school" | "platform";
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex size-9 shrink-0 items-center justify-center rounded-full font-display text-lg leading-none",
        tone === "platform"
          ? "bg-platform-accent text-platform-accent-ink"
          : "bg-action text-on-action",
        className,
      )}
    >
      S
    </span>
  );
}
