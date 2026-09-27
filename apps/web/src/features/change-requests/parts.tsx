import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { formatDate } from "@/lib/format";
import { changeRequestTone, type ChangeRequest, type ChangeRequestStatus } from "./types";

export const DAY_MS = 24 * 60 * 60 * 1000;

export function ChangeRequestStatusBadge({ status }: { status: ChangeRequestStatus }) {
  const t = useTranslations("changeRequests.status");
  return <Badge tone={changeRequestTone[status]}>{t(status)}</Badge>;
}

/** Whole days until `iso` (0 on the last day, negative once passed). */
export function daysUntil(iso: string, now: number = Date.now()): number {
  return Math.floor((new Date(iso).getTime() - now) / DAY_MS);
}

/** Field label in the reader's language. */
export function fieldLabel(request: ChangeRequest, locale: string): string {
  return locale === "te" && request.attribute_label_te
    ? request.attribute_label_te
    : request.attribute_label_en;
}

const GENDERS = ["female", "male", "transgender"] as const;

/** A value as the office reads it: dates DD/MM/YYYY, gender in words, masked stays masked. */
export function DisplayValue({
  value,
  masked,
  attributeKey,
}: {
  value: string | null;
  masked: boolean;
  attributeKey: string;
}) {
  const t = useTranslations("changeRequests");
  if (value === null || value === "") {
    return <span className="text-ink-muted">{t("noValue")}</span>;
  }
  if (masked) {
    return (
      <span>
        <span aria-hidden="true">{value}</span>
        <span className="sr-only">{t("maskedValue")}</span>
      </span>
    );
  }
  const gender = GENDERS.find((item) => item === value);
  if (attributeKey === "gender" && gender) return <>{t(`genderValues.${gender}`)}</>;
  return <>{/^\d{4}-\d{2}-\d{2}$/.test(value) ? formatDate(value) : value}</>;
}

/** "Old → new" on one line, with words for screen readers. */
export function ValueChange({ request }: { request: ChangeRequest }) {
  const t = useTranslations("changeRequests");
  return (
    <span className="flex flex-wrap items-baseline gap-x-2">
      <span className="sr-only">{t("oldValue")}:</span>
      <DisplayValue
        value={request.old_value}
        masked={request.masked}
        attributeKey={request.attribute_key}
      />
      <span aria-hidden="true">→</span>
      <span className="sr-only">{t("newValue")}:</span>
      <strong>
        <DisplayValue
          value={request.new_value}
          masked={request.masked}
          attributeKey={request.attribute_key}
        />
      </strong>
    </span>
  );
}

/** "Expires on 26/10/2026 (in 29 days)" or "Expired". */
export function ExpiryText({ request }: { request: ChangeRequest }) {
  const t = useTranslations("changeRequests");
  if (request.status !== "pending") return null;
  const days = daysUntil(request.expires_at);
  if (days < 0) return <span suppressHydrationWarning>{t("expiredNow")}</span>;
  return (
    <span suppressHydrationWarning>
      {t("expiresOn", { date: formatDate(request.expires_at) ?? "", days: Math.max(days, 0) })}
    </span>
  );
}
