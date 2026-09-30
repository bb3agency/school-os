import type { ComponentProps, ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

/**
 * - `primary` (default) and its alias `primary-dark`: near-black fill, white text ("Save changes").
 * - `brand`: blue fill for the one brand-coloured call to action on a page.
 * - `secondary`: white with a hairline border. `ghost`: text only (blue).
 * - `danger`: red fill for destructive actions ("End", "Revoke").
 * - `inverse`: white fill with dark text, for dark or blue surfaces (AI panel, dark cards).
 *
 * `cn` joins classes without merging them, so `className` can add spacing or width but
 * cannot reliably override colours or padding: pick a variant or size instead.
 */
export type ButtonVariant =
  "primary" | "primary-dark" | "brand" | "secondary" | "danger" | "ghost" | "inverse";
export type ButtonSize = "lg" | "md" | "sm";

/*
 * `.pressable` (globals.css, docs/17 §5.5): the button shrinks to 0.97 while pressed
 * (140ms, strong ease-out), and its colour fades only while a fine pointer hovers it, only
 * without reduced motion. A variant change (e.g. "Preview" becoming secondary when "Promote"
 * takes over) or a keyboard press repaints at once, so there is never a half-faded,
 * low-contrast frame (WCAG 1.4.3; axe used to catch one mid-fade).
 * Labels stay on one line from sm; on phones a long (Telugu) label wraps inside the button
 * instead of pushing it past the screen edge.
 */
const base =
  "pressable inline-flex max-w-full items-center justify-center gap-2 rounded-md border text-center font-medium " +
  "whitespace-normal select-none sm:whitespace-nowrap " +
  "disabled:cursor-not-allowed disabled:opacity-60 aria-disabled:cursor-not-allowed aria-disabled:opacity-60 " +
  "aria-busy:cursor-progress";

const dark =
  "border-action bg-action text-on-action shadow-raised hover:bg-action-hover hover:border-action-hover";

const variants: Record<ButtonVariant, string> = {
  primary: dark,
  "primary-dark": dark,
  brand:
    "border-brand bg-brand text-white shadow-raised hover:bg-brand-strong hover:border-brand-strong",
  secondary: "border-border-soft bg-surface text-ink hover:bg-surface-muted",
  danger: "border-danger bg-danger text-white hover:bg-danger-hover hover:border-danger-hover",
  ghost: "border-transparent bg-transparent text-primary hover:bg-primary-soft",
  inverse: "border-white bg-white text-ink shadow-raised hover:bg-primary-soft",
};

const sizes: Record<ButtonSize, string> = {
  lg: "min-h-12 px-6 py-2.5 text-base",
  md: "min-h-10 px-4 py-2 text-sm",
  sm: "min-h-8 px-3 py-1 text-sm",
};

/** Classes of a button, for links and summaries that must look like one. */
export function buttonClasses(variant: ButtonVariant = "primary", size: ButtonSize = "md"): string {
  return cn(base, variants[variant], sizes[size]);
}

export interface ButtonProps extends ComponentProps<"button"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /**
   * Work in progress: a small spinner before the label and `aria-busy`. Pair it with
   * `disabled` and a label that says so ("Working…"); the spinner is decorative and stands
   * still under reduced motion.
   */
  loading?: boolean;
}

/** Decorative spinner for `loading` buttons (a quarter arc turning; CSS only). */
export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      fill="none"
      className={cn("size-4 shrink-0 motion-safe:animate-spin", className)}
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.3" strokeWidth={3} />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth={3} strokeLinecap="round" />
    </svg>
  );
}

/** Defaults to type="button" so a button never submits a form by accident. */
export function Button({
  variant = "primary",
  size = "md",
  type = "button",
  loading = false,
  className,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      aria-busy={loading || undefined}
      className={cn(buttonClasses(variant, size), className)}
      {...props}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}

export interface ButtonLinkProps {
  href: string;
  children: ReactNode;
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
}

/** A link styled as a button, for navigation actions ("Provision school"). */
export function ButtonLink({
  href,
  children,
  variant = "primary",
  size = "md",
  className,
}: ButtonLinkProps) {
  return (
    <Link href={href} className={cn(buttonClasses(variant, size), className)}>
      {children}
    </Link>
  );
}

export type IconButtonVariant = "secondary" | "ghost" | "primary" | "danger";
export type IconButtonSize = "sm" | "md" | "lg";

const iconVariants: Record<IconButtonVariant, string> = {
  secondary: "border-border-soft bg-surface text-ink hover:bg-surface-muted",
  ghost: "border-transparent bg-transparent text-ink-muted hover:bg-surface-muted hover:text-ink",
  primary: "border-action bg-action text-on-action hover:bg-action-hover",
  danger: "border-danger bg-danger text-white hover:bg-danger-hover",
};

const iconSizes: Record<IconButtonSize, string> = {
  sm: "size-8",
  md: "size-10",
  lg: "size-12",
};

/** Classes of a round icon button (for links that look like one). */
export function iconButtonClasses(
  variant: IconButtonVariant = "secondary",
  size: IconButtonSize = "md",
): string {
  return cn(
    "pressable relative inline-flex shrink-0 items-center justify-center rounded-full border",
    "disabled:cursor-not-allowed disabled:opacity-60",
    iconVariants[variant],
    iconSizes[size],
  );
}

export interface IconButtonProps extends Omit<ComponentProps<"button">, "aria-label" | "children"> {
  /** Accessible name; required because the button shows only an icon. */
  label: string;
  /** The icon (usually `<Icon name="plus" />`). */
  children: ReactNode;
  variant?: IconButtonVariant;
  size?: IconButtonSize;
  /** Show a small dot (e.g. unread). Decorative: say it in `label` too. */
  dot?: boolean;
}

/** Round icon-only button (36–48px, hairline border). Defaults to type="button". */
export function IconButton({
  label,
  children,
  variant = "secondary",
  size = "md",
  dot = false,
  type = "button",
  className,
  ...props
}: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      className={cn(iconButtonClasses(variant, size), className)}
      {...props}
    >
      {children}
      {dot ? (
        <span
          aria-hidden="true"
          className="absolute top-1 right-1 size-2.5 rounded-full border-2 border-surface bg-danger"
        />
      ) : null}
    </button>
  );
}
