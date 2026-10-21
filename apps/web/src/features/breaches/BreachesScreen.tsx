"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { ButtonLink } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDateTime } from "@/lib/format";
import { BreachStatusPill, DutyClockPill, OriginPill, useCategoryNames } from "./parts";
import {
  BREACH_KEYS,
  BREACH_MANAGE,
  BREACH_READ,
  nextDutyClock,
  registerOrder,
  type Breach,
} from "./types";

/** "12 children, 20 guardians, 0 staff" in figures (approximate counts, never names). */
export function PeopleCounts({
  row,
}: {
  row: Pick<Breach, "approx_children" | "approx_guardians" | "approx_staff">;
}) {
  const t = useTranslations("breaches");
  const locale = useLocale() as Locale;
  const items = [
    { key: "children", value: row.approx_children },
    { key: "guardians", value: row.approx_guardians },
    { key: "staff", value: row.approx_staff },
  ] as const;
  return (
    <span className="flex flex-col gap-0.5 text-sm">
      {items.map((item) => (
        <span key={item.key} className="whitespace-nowrap">
          <span className="num font-semibold">{formatCount(item.value, locale)}</span>{" "}
          <span className="text-ink-muted">{t(`people.${item.key}`)}</span>
        </span>
      ))}
    </span>
  );
}

/**
 * Breach register (US-2701, FR-DPDP-030..034, ADR-0050): every personal-data breach the school
 * recorded or SchoolOS reported to it, with the next duty and the time left on its clock
 * ("Board report due in 41 h"). A notice from SchoolOS (the school's processor) sits on top
 * with a warning. Owners and principals (`privacy.breach.manage`) record breaches; auditors
 * read. Descriptions are never shown in the list (they are encrypted and opened one at a time).
 */
export function BreachesScreen() {
  const t = useTranslations("breaches");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(BREACH_READ);
  const categoryNames = useCategoryNames();
  const list = useQuery({
    queryKey: BREACH_KEYS.list,
    queryFn: () => unwrap(api.GET("/api/v1/privacy/breaches")),
    enabled: allowed,
    retry: false,
  });

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        can(BREACH_MANAGE) ? (
          <ButtonLink href="/privacy/breaches/new">{t("record")}</ButtonLink>
        ) : undefined
      }
    />
  );
  if (!meLoaded) {
    return (
      <div className="space-y-5">
        {header}
        <LoadingState label={tc("loading")} />
      </div>
    );
  }
  if (!allowed) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  const rows = registerOrder(list.data ?? []);
  const notices = rows.filter(
    (row) => row.origin === "processor_notice" && row.status !== "closed",
  );
  const columns: ColumnV2<Breach>[] = [
    {
      key: "noticed",
      stack: "title",
      header: t("colNoticed"),
      cell: (row) => (
        <Link
          href={`/privacy/breaches/${row.id}`}
          className="num font-semibold whitespace-nowrap text-primary underline-offset-4 after:absolute after:inset-0 hover:underline"
        >
          {formatDateTime(row.noticed_at)}
        </Link>
      ),
    },
    { key: "origin", header: t("colOrigin"), cell: (row) => <OriginPill origin={row.origin} /> },
    {
      key: "categories",
      header: t("colCategories"),
      cell: (row) => (
        <span className="block max-w-xs text-sm">{categoryNames(row.categories)}</span>
      ),
    },
    { key: "people", header: t("colPeople"), cell: (row) => <PeopleCounts row={row} /> },
    {
      key: "next",
      header: t("colNextDuty"),
      cell: (row) =>
        row.next_duty ? (
          <DutyClockPill duty={row.next_duty.key} clock={nextDutyClock(row.next_duty)} />
        ) : (
          <Value>{row.status === "closed" ? t("noDutyClosed") : t("noDutyLeft")}</Value>
        ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <BreachStatusPill status={row.status} />,
    },
  ];

  return (
    <div className="space-y-5">
      {header}
      {notices.length > 0 ? (
        <Alert tone="warning" title={t("processorAlertTitle", { count: notices.length })}>
          <p>{t("processorAlertBody")}</p>
          <ul className="mt-2 space-y-1">
            {notices.map((row) => (
              <li key={row.id}>
                <Link
                  href={`/privacy/breaches/${row.id}`}
                  className="font-semibold underline underline-offset-4"
                >
                  {t("processorAlertLink", { when: formatDateTime(row.noticed_at) ?? "" })}
                </Link>
              </li>
            ))}
          </ul>
        </Alert>
      ) : null}
      <DataTableV2
        id="privacy-breaches"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...list, data: rows })}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
        onRetry={() => void list.refetch()}
        {...(can(BREACH_MANAGE)
          ? {
              emptyAction: (
                <ButtonLink href="/privacy/breaches/new" variant="secondary">
                  {t("record")}
                </ButtonLink>
              ),
            }
          : {})}
      />
    </div>
  );
}
