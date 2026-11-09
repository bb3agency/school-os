"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { useBffClient } from "@/lib/bff/query";
import { formatCount } from "@/lib/format";
import * as z from "@/lib/zod";
import { FOLLOWUP_KEYS, FOLLOWUP_OWNERS, fetchFollowups, ownerCounts, recordStep } from "./data";

/**
 * The readiness page's follow-up line (FR-UDI-023): "Waiting on parents: 18 (7 overdue)" for
 * the profile in the reader's scope, with a link to the follow-up list.
 */
export function FollowupSummaryLine({ profileKey }: { profileKey: string }) {
  const t = useTranslations("udiseFollowup");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const list = useQuery({
    queryKey: FOLLOWUP_KEYS.list(profileKey, null),
    queryFn: () => fetchFollowups(api, profileKey, null),
    retry: false,
  });
  if (!list.data) return null;
  const count = (value: number) => formatCount(value, locale) ?? "0";
  const parts = FOLLOWUP_OWNERS.flatMap((owner) => {
    const counts = ownerCounts(list.data, owner);
    if (!counts || counts.open === 0) return [];
    return [t(`summary.${owner}`, { open: count(counts.open), overdue: count(counts.overdue) })];
  });
  if (parts.length === 0) return null;
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink print:hidden">
      <span>{parts.join(" · ")}</span>
      <Link
        href={`/findings/readiness/follow-ups?profile=${profileKey}`}
        className="font-semibold text-primary underline-offset-4 hover:underline"
      >
        {t("openList")}
      </Link>
    </p>
  );
}

/**
 * One click "slips given" for the parents of a section (FR-UDI-026): records the step for every
 * student of the section who waits for the parent's Aadhaar update. Shown to holders of
 * `dq.readiness.manage` after the slips are printed.
 */
export function MarkSlipsGiven({
  profileKey,
  studentIds,
}: {
  profileKey: string;
  studentIds: string[];
}) {
  const t = useTranslations("udiseFollowup.slips");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const ids = studentIds.slice(0, 300);
  if (ids.length === 0) return null;
  return (
    <ActionDialog
      triggerLabel={t("trigger")}
      triggerSize="sm"
      title={t("title")}
      description={t("description", { count: formatCount(ids.length, locale) ?? "0" })}
      confirmLabel={t("confirm")}
      schema={z.object({})}
      invalidate={[FOLLOWUP_KEYS.all]}
      errorNamespace="udiseFollowup"
      submit={(_data, key) =>
        recordStep(
          api,
          profileKey,
          { studentIds: ids, owner: "parent_aadhaar", event: "slip_given" },
          key,
        )
      }
    />
  );
}
