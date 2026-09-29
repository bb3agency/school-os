"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Pill, type PillVariant } from "@/components/ui/Badge";

/**
 * How long ago a deployment last reported ("12 min ago"). The age is computed from the
 * API's own timestamp; the colour follows the health status the API computed from its fleet
 * rules (no thresholds are guessed here). Text always says the age.
 */
export function HeartbeatPill({
  at,
  status,
  now,
}: {
  at: string | null | undefined;
  status: string;
  /** For tests; otherwise the time of first render. */
  now?: number;
}) {
  const t = useTranslations("platform.fleet");
  const locale = useLocale();
  const [renderedAt] = useState(() => now ?? Date.now());
  if (!at) return <Pill variant="tag">{t("heartbeatNever")}</Pill>;
  const then = Date.parse(at);
  if (!Number.isFinite(then)) return null;
  const minutes = Math.max(0, Math.round((renderedAt - then) / 60_000));
  const format = new Intl.RelativeTimeFormat(locale === "te" ? "te-IN" : "en-IN", {
    numeric: "auto",
    style: "short",
  });
  const text =
    minutes < 1
      ? t("heartbeatJustNow")
      : minutes < 60
        ? format.format(-minutes, "minute")
        : minutes < 48 * 60
          ? format.format(-Math.round(minutes / 60), "hour")
          : format.format(-Math.round(minutes / 1440), "day");
  const variant: PillVariant =
    status === "healthy" ? "positive" : status === "unreachable" ? "negative" : "tag";
  return <Pill variant={variant}>{text}</Pill>;
}
