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
 * Accent-tinted card for "Ask the school": a large greeting, a line of help, white
 * suggestion chips and a slot for the question box. Ink text on `accent-soft` (and
 * `ink-muted` for the help line) are listed pairs in tokens.test.ts; the focus ring is the
 * normal accent ring (3:1 on the tint). Answers are NOT rendered here: they belong in
 * normal cards with their source chips (CLAUDE.md §10, invariant 8).
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
    "inline-flex min-h-9 items-center gap-1.5 rounded-full border border-border bg-surface px-3.5 py-1.5 text-start text-sm font-semibold text-ink hover:border-accent motion-safe:transition-colors pointer-coarse:min-h-11";
  return (
    <section
      aria-labelledby={headingId}
      className={cn(
        "relative overflow-hidden rounded-xl border border-info-border bg-accent-soft p-5 text-ink md:p-8",
        className,
      )}
    >
      {eyebrow ? (
        <Eyebrow tone="ink" className="mb-2 flex items-center gap-1.5">
          <Icon name="sparkles" className="size-4 text-accent" />
          {eyebrow}
        </Eyebrow>
      ) : null}
      <h2 id={headingId} className="text-2xl text-ink md:text-3xl">
        {greeting}
      </h2>
      {description ? <p className="mt-2 max-w-2xl text-ink-muted">{description}</p> : null}
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
