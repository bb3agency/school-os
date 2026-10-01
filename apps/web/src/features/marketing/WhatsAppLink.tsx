import { useTranslations } from "next-intl";
import { buttonClasses, type ButtonSize, type ButtonVariant } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";
import { whatsappHref } from "./links";

/**
 * "Ask on WhatsApp" (docs/17 §5.6): a secondary call to action beside "Talk to us", shown
 * only when `SOS_PUBLIC_WHATSAPP_NUMBER` is set. A plain link to wa.me with a fixed greeting;
 * nothing is stored and no personal data is sent. The accessible name starts with the visible
 * label and says that it opens WhatsApp (WCAG 2.5.3). Works in server and client components.
 */
export function WhatsAppLink({
  number,
  variant = "secondary",
  size = "lg",
  context,
  className,
}: {
  /** Validated digits from the settings. */
  number: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Extra words for screen readers when several links share a page (e.g. the plan name). */
  context?: string;
  className?: string;
}) {
  const t = useTranslations("marketing.cta");
  return (
    <a
      href={whatsappHref(number, t("whatsappMessage"))}
      rel="noopener noreferrer"
      className={cn(buttonClasses(variant, size), "mk-press", className)}
    >
      <Icon name="message" className="size-4.5" />
      {t("whatsapp")}
      <span className="sr-only">
        {context ? `: ${context}` : null} {t("whatsappOpens")}
      </span>
    </a>
  );
}
