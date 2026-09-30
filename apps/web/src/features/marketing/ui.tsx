import type { ReactNode } from "react";
import { buttonClasses } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { SIGN_IN_HREF, mailtoHref } from "./links";

/**
 * Layout pieces of the public pages (docs/17 §5.6). One spacing scale: sections are
 * py-20/md:py-28, section intros mb-12/md:mb-16, card grids gap-4/md:gap-5; the content
 * column is max-w-7xl (80rem) inside the page gutter.
 */
export function Container({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("px-page mx-auto w-full max-w-7xl", className)}>{children}</div>;
}

export function Section({
  id,
  labelledBy,
  children,
  className,
  tone = "plain",
}: {
  id?: string;
  labelledBy: string;
  children: ReactNode;
  className?: string;
  tone?: "plain" | "white" | "night";
}) {
  return (
    <section
      id={id}
      aria-labelledby={labelledBy}
      className={cn(
        "scroll-mt-20 py-20 md:py-28 print:py-6",
        tone === "white" && "border-y border-border bg-surface",
        tone === "night" && "mk-night",
        className,
      )}
    >
      {children}
    </section>
  );
}

/** Mono eyebrow, the section's h2 and an optional intro, left or centred. */
export function SectionIntro({
  id,
  eyebrow,
  title,
  intro,
  align = "left",
  night = false,
  level = 2,
}: {
  id: string;
  eyebrow?: string;
  title: string;
  intro?: string | undefined;
  align?: "left" | "center";
  night?: boolean;
  level?: 1 | 2;
}) {
  const Heading = level === 1 ? "h1" : "h2";
  return (
    <div className={cn("mb-12 max-w-3xl md:mb-16", align === "center" && "mx-auto text-center")}>
      {eyebrow ? (
        <p className={cn("eyebrow mb-4", night ? "mk-night-muted" : "text-primary")}>{eyebrow}</p>
      ) : null}
      <Heading
        id={id}
        className={cn(
          "mk-title font-normal",
          level === 1
            ? "text-4xl md:text-5xl lg:text-6xl"
            : "text-3xl md:text-4xl lg:text-[2.75rem]",
          night ? "text-white" : "text-ink",
        )}
      >
        {title}
      </Heading>
      {intro ? (
        <p
          className={cn(
            "mk-lede mt-5 text-lg md:text-xl",
            night ? "mk-night-muted" : "text-ink-muted",
            align === "center" && "mx-auto max-w-2xl",
          )}
        >
          {intro}
        </p>
      ) : null}
    </div>
  );
}

/** Top of an inner page: the page's one h1 on the hero backdrop, with room for extras. */
export function PageHero({
  id,
  eyebrow,
  title,
  intro,
  children,
}: {
  id: string;
  eyebrow: string;
  title: string;
  intro: string;
  children?: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="mk-hero-bg relative -mt-16 overflow-hidden pt-16">
      <div aria-hidden="true" className="mk-grid pointer-events-none absolute inset-0" />
      <Container className="relative pt-12 pb-16 md:pt-20 md:pb-24">
        <p className="eyebrow text-primary">{eyebrow}</p>
        <h1
          id={id}
          className="mk-display mt-5 max-w-4xl text-[2.5rem] font-extralight text-ink sm:text-5xl lg:text-6xl"
        >
          {title}
        </h1>
        <p className="mk-lede mt-6 max-w-2xl text-lg text-ink-muted md:text-xl">{intro}</p>
        {children}
      </Container>
    </section>
  );
}

export function PlannedBadge({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-info-border bg-info-soft px-2.5 py-0.5 text-xs font-medium text-info-ink">
      <Icon name="clock" className="size-3.5" />
      {label}
    </span>
  );
}

/** Tag on every illustration: the data in it is made up. */
export function SampleTag({ label, className }: { label: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border border-warning-border/40 bg-warning-soft px-2 py-0.5 font-mono text-[0.6875rem] font-medium tracking-wide text-warning-ink uppercase",
        className,
      )}
    >
      {label}
    </span>
  );
}

export function CheckList({ items, night = false }: { items: string[]; night?: boolean }) {
  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item} className="flex gap-3">
          <span
            className={cn(
              "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full",
              night ? "bg-white/10 text-white" : "bg-success-soft text-success-ink",
            )}
          >
            <Icon name="check" className="size-3.5" />
          </span>
          <span className={night ? "mk-night-muted" : "text-ink"}>{item}</span>
        </li>
      ))}
    </ul>
  );
}

/** Text link with an arrow that nudges on hover (pointer devices only). */
export function ArrowLink({
  href,
  children,
  night = false,
}: {
  href: string;
  children: ReactNode;
  night?: boolean;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "inline-flex min-h-11 items-center gap-1.5 font-medium underline-offset-4 hover:underline",
        night ? "text-white" : "text-primary",
      )}
    >
      {children}
      <Icon name="arrowRight" className="mk-arrow size-4" />
    </Link>
  );
}

/**
 * The page's calls to action: "Talk to us" (mailto, only when an address is configured) and
 * "Sign in" for existing schools. Without an address, "Sign in" becomes the primary button.
 */
export function CtaGroup({
  contactEmail,
  talkLabel,
  signInLabel,
  secondary,
  inverse = false,
  className,
}: {
  contactEmail: string | null;
  talkLabel: string;
  signInLabel: string;
  secondary?: { href: string; label: string };
  inverse?: boolean;
  className?: string;
}) {
  const first = inverse ? "inverse" : "primary";
  return (
    <div className={cn("flex flex-wrap gap-3", className)} data-print="hide">
      {contactEmail ? (
        <a href={mailtoHref(contactEmail)} className={cn(buttonClasses(first, "lg"), "mk-press")}>
          {talkLabel}
          <Icon name="arrowRight" className="mk-arrow size-4.5" />
        </a>
      ) : null}
      <a
        href={SIGN_IN_HREF}
        className={cn(
          buttonClasses(contactEmail ? (inverse ? "inverse" : "secondary") : first, "lg"),
          "mk-press",
        )}
      >
        {signInLabel}
      </a>
      {secondary && !contactEmail ? (
        // An in-page anchor: a plain link (no locale handling needed).
        <a
          href={secondary.href}
          className={cn(buttonClasses(inverse ? "inverse" : "secondary", "lg"), "mk-press")}
        >
          {secondary.label}
        </a>
      ) : null}
    </div>
  );
}
