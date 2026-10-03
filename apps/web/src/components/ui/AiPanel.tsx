import { useId, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Eyebrow } from "./Eyebrow";
import { Icon } from "./Icon";

export interface AiSuggestion {
  id: string;
  label: ReactNode;
  /** A link (e.g. `/ask?q=…`) … */
  href?: string;
  /** … or an action in a client component. */
  onSelect?: () => void;
}

/**
 * Blue gradient card for "Ask the school" (to be adopted by the Ask screens): a large
 * greeting, a line of help, white suggestion chips and a slot for the question box.
 * White text on the lightest gradient stop is 5.2:1. Answers are NOT rendered here:
 * they belong in normal cards with their source chips (CLAUDE.md §10, invariant 8).
 */
export function AiPanel({
  greeting,
  eyebrow,
  description,
  suggestions = [],
  suggestionsLabel,
  children,
  className,
}: {
  greeting: ReactNode;
  eyebrow?: ReactNode;
  description?: ReactNode;
  suggestions?: readonly AiSuggestion[];
  /** Accessible name of the suggestion list ("Try asking"). */
  suggestionsLabel?: string;
  /** Question form or other controls under the chips. */
  children?: ReactNode;
  className?: string;
}) {
  const headingId = useId();
  const chip =
    "inline-flex min-h-9 items-center gap-1.5 rounded-full border border-white bg-white px-3.5 py-1.5 text-left text-sm font-semibold text-ink shadow-raised transition-colors hover:bg-primary-soft";
  return (
    <section
      aria-labelledby={headingId}
      className={cn(
        "ai-gradient ai-chrome relative overflow-hidden rounded-xl border border-transparent p-6 text-white shadow-card md:p-8",
        className,
      )}
    >
      {eyebrow ? (
        <Eyebrow tone="inverse" className="mb-2 flex items-center gap-1.5">
          <Icon name="sparkles" className="size-4" />
          {eyebrow}
        </Eyebrow>
      ) : null}
      <h2 id={headingId} className="text-2xl md:text-3xl">
        {greeting}
      </h2>
      {description ? <p className="mt-2 max-w-2xl text-white">{description}</p> : null}
      {suggestions.length > 0 ? (
        <ul aria-label={suggestionsLabel} className="mt-5 flex flex-wrap gap-2">
          {suggestions.map((suggestion) => (
            <li key={suggestion.id}>
              {suggestion.href ? (
                <a href={suggestion.href} className={chip}>
                  {suggestion.label}
                </a>
              ) : (
                <button type="button" onClick={suggestion.onSelect} className={chip}>
                  {suggestion.label}
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {children ? <div className="mt-5">{children}</div> : null}
    </section>
  );
}

/**
 * Pale green quote block for a suggested reply or a quoted source line, with a mono
 * label ("PROACTIVE · ASK PARENT").
 */
export function QuoteBlock({
  label,
  children,
  className,
}: {
  label?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <figure
      className={cn(
        "quote-gradient rounded-lg border border-positive-border px-4 py-3 text-positive-ink",
        className,
      )}
    >
      {label ? <figcaption className="eyebrow mb-1 text-positive-ink">{label}</figcaption> : null}
      <blockquote className="text-sm text-ink">{children}</blockquote>
    </figure>
  );
}
