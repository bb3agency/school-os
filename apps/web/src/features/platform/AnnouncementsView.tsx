"use client";

import type { Announcement } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Badge, Pill, type BadgeTone } from "@/components/ui/Badge";
import { Card, cardClasses } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { announcementTone, known } from "@/features/status";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { cn } from "@/lib/cn";
import { AnnouncementEditor } from "./AnnouncementEditor";
import { PK, useCan } from "./data";
import { MonoTime } from "./pills";

const SEVERITIES = ["info", "maintenance", "warning", "critical"] as const;
const SEVERITY_TONE: Record<(typeof SEVERITIES)[number], BadgeTone> = {
  info: "info",
  maintenance: "violet",
  warning: "warning",
  critical: "danger",
};

/** FR-PLT-026: EN/TE banners to all schools, a tier or listed schools, scheduled. */
export function AnnouncementsScreen() {
  const t = useTranslations("platform.announcements");
  const tn = useTranslations("platform.nav");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const tstatus = useTranslations("status.announcement");
  const locale = useLocale();
  const api = useBffClient("operator");
  const can = useCan();
  const manage = can("platform.announcements.manage");
  const announcements = useApiQuery(
    [...PK.announcements, "list"],
    async () => (await unwrap(api.GET("/api/v1/platform/announcements"))).data,
  );

  const audienceLabel = (row: Announcement) => {
    if (row.audience === "all") return t("audienceAll");
    if (row.audience === "tier") {
      return row.audience_tier === "dedicated" ? t("audienceDedicated") : t("audienceShared");
    }
    return t("audienceTenantsCount", { count: row.audience_tenant_ids.length });
  };

  function item(row: Announcement) {
    const severity = SEVERITIES.find((candidate) => candidate === row.severity);
    const status = known(announcementTone, row.status);
    return (
      <li
        key={row.id}
        className={cn(cardClasses({ padding: "sm", tone: "outline" }), "flex flex-col gap-3")}
      >
        <div className="flex flex-wrap items-center gap-2">
          {severity ? (
            <Badge tone={SEVERITY_TONE[severity]}>{t(`severities.${severity}`)}</Badge>
          ) : (
            <Badge>{row.severity}</Badge>
          )}
          {status ? (
            <Badge tone={announcementTone[status]}>{tstatus(status)}</Badge>
          ) : (
            <Badge>{row.status}</Badge>
          )}
          <Pill variant="tag">{audienceLabel(row)}</Pill>
        </div>
        <div className="space-y-1">
          <h3 lang={locale} className="font-medium text-ink">
            {locale === "te" ? row.title_te : row.title_en}
          </h3>
          <p lang={locale} className="text-sm whitespace-pre-wrap text-ink-muted">
            {locale === "te" ? row.body_te : row.body_en}
          </p>
        </div>
        <dl className="grid grid-cols-label-value gap-x-3 gap-y-1 text-sm">
          <dt className="text-ink-muted">{t("colStarts")}</dt>
          <dd>
            <MonoTime value={row.starts_at} />
          </dd>
          <dt className="text-ink-muted">{t("colEnds")}</dt>
          <dd>
            <MonoTime value={row.ends_at} />
          </dd>
        </dl>
        {manage && row.status !== "cancelled" ? (
          <div className="mt-auto flex justify-end border-t border-border pt-3">
            <ActionDialog
              triggerLabel={t("cancelAnnouncement")}
              triggerSize="sm"
              triggerVariant="ghost"
              triggerDescription={row.title_en}
              title={t("cancelTitle")}
              description={t("cancelBody")}
              confirmLabel={t("cancelAnnouncement")}
              confirmVariant="danger"
              schema={z.object({})}
              invalidate={[PK.announcements]}
              submit={() =>
                unwrap(
                  api.POST("/api/v1/platform/announcements/{announcement_id}/cancel", {
                    params: { path: { announcement_id: row.id } },
                  }),
                )
              }
            />
          </div>
        ) : null}
      </li>
    );
  }

  function list() {
    if (announcements.status === "loading") return <LoadingState label={tc("loading")} />;
    if (announcements.status === "error") {
      return (
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {announcements.reason ? te(`load.${announcements.reason}`) : tc("loadErrorBody")}
        </Alert>
      );
    }
    if (announcements.status === "unavailable") {
      return <EmptyState title={tc("notAvailableYetTitle")} body={tc("notAvailableYetBody")} />;
    }
    if (announcements.data.length === 0) {
      return <EmptyState icon="megaphone" title={t("emptyTitle")} body={t("emptyBody")} />;
    }
    return (
      <ul aria-label={t("listTitle")} className="grid gap-4 lg:grid-cols-2">
        {announcements.data.map(item)}
      </ul>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("dashboard"), href: "/platform" }, { label: t("title") }]}
      />
      {manage ? (
        <Card title={t("newTitle")}>
          <AnnouncementEditor />
        </Card>
      ) : null}
      <Card title={t("listTitle")}>{list()}</Card>
    </div>
  );
}
