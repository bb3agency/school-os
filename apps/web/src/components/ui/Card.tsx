import { useId, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Eyebrow } from "./Eyebrow";

export type CardPadding = "none" | "sm" | "md" | "lg";
export type CardTone = "default" | "muted" | "outline";

const paddings: Record<CardPadding, string> = {
  none: "p-0",
  sm: "p-3 sm:p-4",
  md: "p-4 sm:p-5 lg:p-6",
  lg: "p-5 sm:p-6 lg:p-8",
};

const cardTones: Record<CardTone, string> = {
  default: "border-border bg-surface shadow-card",
  muted: "border-border bg-surface-muted",
  outline: "border-border bg-transparent",
};

/**
 * Classes of the white card surface, for elements that are not a `<section>`. `min-w-0`
 * lets a card in a grid or flex row shrink to its track: a wide table inside then scrolls
 * in its own region instead of pushing the card (and the page) past the screen edge.
 */
export function cardClasses({
  padding = "md",
  tone = "default",
}: { padding?: CardPadding; tone?: CardTone } = {}): string {
  return cn(
    "min-w-0 rounded-xl border print:rounded-none print:border-black print:bg-white print:p-3 print:shadow-none",
    cardTones[tone],
    paddings[padding],
  );
}

export interface CardHeaderProps {
  title?: ReactNode;
  /** Mono uppercase label above the title ("COPILOT LOG"). */
  eyebrow?: ReactNode;
  description?: ReactNode;
  /** Buttons, filters or a menu on the right. Hidden in print. */
  actions?: ReactNode;
  /** Heading level for the title; pages use h1, so sections are h2 by default. */
  headingLevel?: 2 | 3;
  /** Id for the heading, so a wrapper can use it in aria-labelledby. */
  headingId?: string;
  className?: string;
}

/** Title row of a card: eyebrow, title (18px medium), description and actions. */
export function CardHeader({
  title,
  eyebrow,
  description,
  actions,
  headingLevel = 2,
  headingId,
  className,
}: CardHeaderProps) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div className={cn("mb-4 flex flex-wrap items-start justify-between gap-3", className)}>
      <div className="min-w-0 flex-1 basis-60 space-y-1">
        {eyebrow ? <Eyebrow>{eyebrow}</Eyebrow> : null}
        {title ? (
          <Heading id={headingId} className="text-lg font-medium text-ink">
            {title}
          </Heading>
        ) : null}
        {description ? <p className="text-sm text-ink-muted">{description}</p> : null}
      </div>
      {actions ? (
        <div className="flex max-w-full min-w-0 flex-wrap items-center gap-2" data-print="hide">
          {actions}
        </div>
      ) : null}
    </div>
  );
}

export interface CardProps {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  /** Mono uppercase label above the title. */
  eyebrow?: ReactNode;
  children?: ReactNode;
  className?: string;
  /** Heading level for the title; pages use h1, so sections are h2 by default. */
  headingLevel?: 2 | 3;
  padding?: CardPadding;
  tone?: CardTone;
}

/**
 * White card (20px radius, hairline border, soft shadow). With a title it is a
 * `<section>` named by its heading. Prints as a plain bordered box.
 */
export function Card({
  title,
  description,
  actions,
  eyebrow,
  children,
  className,
  headingLevel = 2,
  padding = "md",
  tone = "default",
}: CardProps) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={title ? headingId : undefined}
      className={cn(cardClasses({ padding, tone }), className)}
    >
      {title || actions || eyebrow ? (
        <CardHeader
          title={title}
          eyebrow={eyebrow}
          description={description}
          actions={actions}
          headingLevel={headingLevel}
          headingId={headingId}
        />
      ) : null}
      {children}
    </section>
  );
}
