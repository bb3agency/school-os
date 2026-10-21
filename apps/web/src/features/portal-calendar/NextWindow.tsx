"use client";

import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { CalendarStateBadge } from "./CalendarScreen";
import type { PortalWindow } from "./data";

/**
 * "Next window: AP SSC March 2027 nominal roll corrections, closes in 107 days" on the readiness
 * screen (US-3104, FR-RDY-009), with its state and a link to the portal calendar.
 */
export function NextWindow({ window }: { window: PortalWindow }) {
  const t = useTranslations("portalCalendar");
  return (
    <p
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink"
      data-testid="next-window"
    >
      <span>
        {t("nextWindow", { event: window.label_en })}{" "}
        <span className="num">
          {window.state === "closed" ? null : t("closesIn", { days: window.days_left })}
        </span>
      </span>
      <CalendarStateBadge state={window.state} />
      <Link
        href="/findings/calendar"
        className="font-semibold text-primary underline-offset-4 hover:underline print:hidden"
      >
        {t("openCalendar")}
      </Link>
    </p>
  );
}
