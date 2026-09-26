import type { Announcement } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { announcementTone } from "@/features/status";
import { formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { AnnouncementEditor } from "./AnnouncementEditor";

/** FR-PLT-026: EN/TE banners to all schools or a tier, scheduled. */
export function AnnouncementsView({
  announcements,
}: {
  announcements: Loadable<readonly Announcement[]>;
}) {
  const t = useTranslations("platform.announcements");
  const tstatus = useTranslations("status.announcement");
  const locale = useLocale();

  const audienceLabel = (audience: Announcement["audience"]) => {
    switch (audience) {
      case "all":
        return t("audienceAll");
      case "shared":
        return t("audienceShared");
      case "dedicated":
        return t("audienceDedicated");
      default:
        return audience;
    }
  };

  const columns: Column<Announcement>[] = [
    {
      key: "title",
      header: t("colTitle"),
      cell: (row) => <span lang={locale}>{locale === "te" ? row.title_te : row.title_en}</span>,
    },
    { key: "audience", header: t("colAudience"), cell: (row) => audienceLabel(row.audience) },
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
      cell: (row) => <Badge tone={announcementTone[row.status]}>{tstatus(row.status)}</Badge>,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <Card title={t("newTitle")}>
        <AnnouncementEditor />
      </Card>
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
