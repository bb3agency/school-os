import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { Icon } from "./Icon";

export interface Step {
  id: string;
  label: string;
  /** A finished step may link back to itself (imports, provisioning). */
  href?: string;
  /** The job stopped at this step (an import that failed at upload): a danger marker with an
      alert icon and "Stopped" read out, never colour alone. Steps after it stay upcoming. */
  failed?: boolean;
}

/**
 * Where the person is in a multi-step job (imports, provisioning, promotions, certificate
 * issue, change requests, exports; docs/17 §4.4). An ordered list: finished steps show a
 * check and "Done", the current one `aria-current="step"`, later ones are muted. It never
 * relies on colour: each state also has an icon or a number and hidden text. Wraps on phones.
 */
export function Steps({
  steps,
  current,
  label,
  className,
}: {
  steps: readonly Step[];
  /** Index of the current step (0-based); steps before it are done. */
  current: number;
  /** Names the list ("Import steps"). */
  label: string;
  className?: string;
}) {
  const t = useTranslations("ui.steps");
  return (
    <ol aria-label={label} className={cn("flex flex-wrap items-center gap-x-2 gap-y-2", className)}>
      {steps.map((step, index) => {
        const state = step.failed
          ? "failed"
          : index < current
            ? "done"
            : index === current
              ? "current"
              : "upcoming";
        const marker = (
          <span
            aria-hidden="true"
            className={cn(
              "num flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
              state === "done" && "bg-accent text-on-accent",
              state === "current" && "border-2 border-accent bg-surface text-accent",
              state === "upcoming" && "border border-border-control bg-surface text-ink-muted",
              state === "failed" && "bg-danger text-white",
            )}
          >
            {state === "done" ? (
              <Icon name="check" className="size-3.5" />
            ) : state === "failed" ? (
              <Icon name="alert" className="size-3.5" />
            ) : (
              index + 1
            )}
          </span>
        );
        const text = (
          <>
            {marker}
            <span
              className={cn(
                "text-sm",
                state === "current"
                  ? "font-semibold text-ink"
                  : state === "failed"
                    ? "font-semibold text-danger"
                    : "text-ink-muted",
              )}
            >
              {step.label}
            </span>
            <span className="sr-only">
              {state === "done"
                ? t("done")
                : state === "failed"
                  ? t("failed")
                  : state === "current"
                    ? t("current")
                    : t("upcoming")}
            </span>
          </>
        );
        return (
          <li
            key={step.id}
            aria-current={
              state === "current" || (state === "failed" && index === current) ? "step" : undefined
            }
            className="flex items-center gap-2"
          >
            {state === "done" && step.href ? (
              <Link
                href={step.href}
                className="inline-flex min-h-8 items-center gap-2 rounded-md hover:underline pointer-coarse:min-h-11"
              >
                {text}
              </Link>
            ) : (
              <span className="inline-flex items-center gap-2">{text}</span>
            )}
            {index < steps.length - 1 ? (
              <span aria-hidden="true" className="h-px w-6 bg-border-soft max-sm:hidden" />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
