import { useTranslations } from "next-intl";
import { buttonClasses } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";
import { mailtoHref } from "./links";

export const PLANS = ["shared", "dedicated"] as const;
const POINTS = ["one", "two", "three", "four"] as const;

/**
 * The two plans (docs/16, ADR-0015): Shared on a white card, Dedicated on the dark card.
 * Never a price: the price is agreed with each school (owner decision). `compact` for the
 * home page teaser; the pricing page adds who it is for and the "Talk to us" link.
 */
export function PlanCards({
  headingLevel = 2,
  compact = false,
  contactEmail = null,
}: {
  headingLevel?: 2 | 3;
  compact?: boolean;
  contactEmail?: string | null;
}) {
  const t = useTranslations("marketing");
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <>
      {PLANS.map((plan) => {
        const dark = plan === "dedicated";
        return (
          <article
            key={plan}
            aria-labelledby={`plan-${plan}`}
            className={cn(
              "flex h-full flex-col rounded-2xl border p-6 md:p-8 print:break-inside-avoid",
              dark ? "mk-night border-transparent" : "border-border bg-surface shadow-card",
            )}
          >
            <span
              aria-hidden="true"
              className={cn(
                "flex size-11 items-center justify-center rounded-xl",
                dark ? "bg-white/10 text-white" : "bg-primary-soft text-primary",
              )}
            >
              <Icon name={dark ? "server" : "layers"} className="size-5" />
            </span>
            <Heading
              id={`plan-${plan}`}
              className={cn(
                "mk-title mt-5 text-2xl font-normal md:text-3xl",
                dark ? "text-white" : "text-ink",
              )}
            >
              {t(`pricing.${plan}.name`)}
            </Heading>
            <p className={cn("mt-3", dark ? "mk-night-muted" : "text-ink-muted")}>
              {t(`pricing.${plan}.summary`)}
            </p>
            {!compact ? (
              <div className={cn("mt-6 rounded-xl p-4", dark ? "bg-white/5" : "bg-surface-muted")}>
                <p className={cn("eyebrow", dark ? "mk-night-muted" : "text-ink-subtle")}>
                  {t("pricing.forLabel")}
                </p>
                <p className={cn("mt-1", dark ? "text-white" : "text-ink")}>
                  {t(`pricing.${plan}.for`)}
                </p>
              </div>
            ) : null}
            {!compact ? (
              <p className={cn("eyebrow mt-6", dark ? "mk-night-muted" : "text-ink-subtle")}>
                {t("pricing.includedLabel")}
              </p>
            ) : null}
            <ul className={cn("flex-1 space-y-3", compact ? "mt-6" : "mt-3")}>
              {POINTS.map((point) => (
                <li key={point} className="flex gap-3">
                  <Icon
                    name="checkCircle"
                    className={cn("mt-0.5 size-5", dark ? "text-positive-border" : "text-success")}
                  />
                  <span className={dark ? "text-white" : "text-ink"}>
                    {t(`pricing.${plan}.points.${point}`)}
                  </span>
                </li>
              ))}
            </ul>
            {!compact ? (
              <div
                className={cn(
                  "mt-8 flex flex-wrap items-center justify-between gap-4 border-t pt-6",
                  dark ? "border-white/10" : "border-border",
                )}
              >
                <p className={cn("font-medium", dark ? "text-white" : "text-ink")}>
                  {t("pricing.priceNote")}
                </p>
                {contactEmail ? (
                  <a
                    href={mailtoHref(contactEmail)}
                    className={cn(buttonClasses(dark ? "inverse" : "primary", "md"), "mk-press")}
                  >
                    {t("cta.talk")}
                    <span className="sr-only">: {t(`pricing.${plan}.name`)}</span>
                  </a>
                ) : null}
              </div>
            ) : null}
          </article>
        );
      })}
    </>
  );
}
