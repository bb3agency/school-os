"use client";

import type { Announcement } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { announcementTone, known } from "@/features/status";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatDateTime } from "@/lib/format";
import { AnnouncementEditor } from "./AnnouncementEditor";
import { PK, useCan } from "./data";

/** FR-PLT-026: EN/TE banners to all schools, a tier or listed schools, scheduled. */
export function AnnouncementsScreen() {
  const t = useTranslations("platform.announcements");
  const tc = useTranslations("common");
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

  const columns: Column<Announcement>[] = [
    {
      key: "title",
      header: t("colTitle"),
      cell: (row) => <span lang={locale}>{locale === "te" ? row.title_te : row.title_en}</span>,
    },
    {
      key: "severity",
      header: t("severity"),
      cell: (row) => {
        const value = (["info", "maintenance", "warning", "critical"] as const).find(
          (candidate) => candidate === row.severity,
        );
        return value ? t(`severities.${value}`) : row.severity;
      },
    },
    { key: "audience", header: t("colAudience"), cell: audienceLabel },
    {
      key: "starts",
      header: t("colStarts"),
      cell: (row) => <Value>{formatDateTime(row.starts_at)}</Value>,
    },
    {
      key: "ends",
      header: t("colEnds"),
      cell: (row) => <Value>{formatDateTime(row.ends_at)}</Value>,
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => {
        const value = known(announcementTone, row.status);
        return value ? (
          <Badge tone={announcementTone[value]}>{tstatus(value)}</Badge>
        ) : (
          <Badge>{row.status}</Badge>
        );
      },
    },
    ...(manage
      ? [
          {
            key: "actions",
            header: tc("actions"),
            cell: (row: Announcement) =>
              row.status !== "cancelled" ? (
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
              ) : null,
          },
        ]
      : []),
  ];

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      {manage ? (
        <Card title={t("newTitle")}>
          <AnnouncementEditor />
        </Card>
      ) : null}
      <Card title={t("listTitle")}>
        <DataTable
          caption={t("listTitle")}
          captionHidden
          columns={columns}
          state={announcements}
          rowKey={(row) => row.id}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
        />
      </Card>
    </div>
  );
}
