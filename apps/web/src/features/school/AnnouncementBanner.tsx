"use client";

import type { AnnouncementBrief } from "@schoolos/api-client";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Alert, type AlertTone } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formatDateTime } from "@/lib/format";

/** Dismissed announcement ids, for this browser tab only (no personal data). */
const DISMISSED_KEY = "sos:dismissed-announcements";

function readDismissed(): string[] {
  try {
    const raw = window.sessionStorage.getItem(DISMISSED_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function writeDismissed(ids: string[]): void {
  try {
    window.sessionStorage.setItem(DISMISSED_KEY, JSON.stringify(ids.slice(-50)));
  } catch {
    // Storage blocked: the banner simply comes back on the next page load.
  }
}

const TONE: Record<string, AlertTone> = {
  info: "info",
  maintenance: "info",
  warning: "warning",
  critical: "danger",
};

/**
 * FR-PLT-026 (docs/16 §14): platform announcements for this school, in the UI language
 * (English or Telugu). Each can be dismissed for the rest of the browser session. Loading
 * or failing quietly shows nothing: banners never block school work.
 */
export function AnnouncementBanner() {
  const t = useTranslations("school.announcements");
  const locale = useLocale();
  const api = useBffClient("staff");
  const [dismissed, setDismissed] = useState<string[] | null>(null);
  const query = useQuery({
    queryKey: ["staff", "announcements"],
    queryFn: () => unwrap(api.GET("/api/v1/announcements")),
    staleTime: 5 * 60_000,
    retry: false,
  });

  useEffect(() => {
    setDismissed(readDismissed());
  }, []);

  if (!query.data || dismissed === null) return null;
  const visible = query.data.filter((item) => !dismissed.includes(item.id));
  if (visible.length === 0) return null;

  function dismiss(item: AnnouncementBrief) {
    const next = [...(dismissed ?? []), item.id];
    writeDismissed(next);
    setDismissed(next);
  }

  return (
    <section aria-label={t("label")} className="mb-6 space-y-3" data-print="hide">
      {visible.map((item) => {
        const title = locale === "te" ? item.title_te : item.title_en;
        const body = locale === "te" ? item.body_te : item.body_en;
        return (
          <Alert key={item.id} tone={TONE[item.severity] ?? "info"} title={title}>
            <p lang={locale}>{body}</p>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs">
                {t("until", { date: formatDateTime(item.ends_at) ?? "" })}
              </span>
              <Button size="sm" variant="ghost" onClick={() => dismiss(item)}>
                {t("dismiss")}
                <span className="sr-only">: {title}</span>
              </Button>
            </div>
          </Alert>
        );
      })}
    </section>
  );
}
