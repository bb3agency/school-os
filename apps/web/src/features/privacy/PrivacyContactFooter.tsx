import type { components } from "@schoolos/api-client";
import { useTranslations } from "next-intl";

type Contact = components["schemas"]["PrivacyContactOut"];

/**
 * The school's privacy and grievance contact at the foot of every staff page (FR-DPDP-004,
 * PRV-011), from GET /me `settings.privacy_contact`. Nothing is shown until the school sets it.
 */
export function PrivacyContactFooter({ contact }: { contact: Contact | null | undefined }) {
  const t = useTranslations("privacy.footer");
  if (!contact?.name) return null;
  const parts = [contact.name, contact.phone, contact.email].filter(
    (part): part is string => typeof part === "string" && part.trim() !== "",
  );
  return (
    <footer className="mt-10 border-t border-border pt-3 text-xs text-ink-muted" data-print="hide">
      <p className="break-words">
        {t("label")} <span className="text-ink">{parts.join(" · ")}</span>
      </p>
    </footer>
  );
}
