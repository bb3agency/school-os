import type { AcademicYear, SchoolClass, Section } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { formatDate } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

export interface StructureViewProps {
  years: Loadable<readonly AcademicYear[]>;
  classes: Loadable<readonly SchoolClass[]>;
  sections: Loadable<readonly Section[]>;
}

/** Class name in the UI language (the API stores English and Telugu names). */
export function className(schoolClass: SchoolClass, locale: string): string {
  return locale === "te" && schoolClass.display_te
    ? schoolClass.display_te
    : schoolClass.display_en;
}

/** US-202 / FR-TEN-010: academic years, classes and sections (writes: tenant.structure.manage). */
export function StructureView({ years, classes, sections }: StructureViewProps) {
  const t = useTranslations("school.structure");
  const tc = useTranslations("common");
  const locale = useLocale();

  const classById = new Map(
    classes.status === "ready" ? classes.data.map((row) => [row.id, row] as const) : [],
  );
  const sectionCount = (classId: string): string | null =>
    sections.status === "ready"
      ? String(sections.data.filter((section) => section.class_id === classId).length)
      : null;

  const yearColumns: Column<AcademicYear>[] = [
    { key: "label", header: t("years.colYear"), cell: (row) => row.label },
    {
      key: "starts",
      header: t("years.colStarts"),
      cell: (row) => <Value>{formatDate(row.starts_on)}</Value>,
    },
    {
      key: "ends",
      header: t("years.colEnds"),
      cell: (row) => <Value>{formatDate(row.ends_on)}</Value>,
    },
    {
      key: "current",
      header: t("years.colCurrent"),
      cell: (row) =>
        row.is_current ? <Badge tone="success">{t("currentBadge")}</Badge> : tc("no"),
    },
  ];
  const classColumns: Column<SchoolClass>[] = [
    { key: "name", header: t("classes.colName"), cell: (row) => className(row, locale) },
    { key: "order", header: t("classes.colOrder"), cell: (row) => row.sort_order },
    {
      key: "sections",
      header: t("classes.colSections"),
      cell: (row) => <Value>{sectionCount(row.id)}</Value>,
    },
  ];
  const sectionColumns: Column<Section>[] = [
    {
      key: "class",
      header: t("sections.colClass"),
      cell: (row) => {
        const parent = classById.get(row.class_id);
        return <Value>{parent ? className(parent, locale) : null}</Value>;
      },
    },
    { key: "name", header: t("sections.colName"), cell: (row) => row.name },
  ];

  return (
    <div className="space-y-6">
      <header className="max-w-3xl space-y-1">
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-ink-muted">{t("description")}</p>
      </header>
      <Card title={t("years.title")} actions={<Button disabled>{t("years.add")}</Button>}>
        <DataTable
          caption={t("years.title")}
          captionHidden
          columns={yearColumns}
          state={years}
          rowKey={(row) => row.id}
          emptyTitle={t("years.emptyTitle")}
          emptyBody={t("years.emptyBody")}
        />
      </Card>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card title={t("classes.title")} actions={<Button disabled>{t("classes.add")}</Button>}>
          <DataTable
            caption={t("classes.title")}
            captionHidden
            columns={classColumns}
            state={classes}
            rowKey={(row) => row.id}
            emptyTitle={t("classes.emptyTitle")}
            emptyBody={t("classes.emptyBody")}
          />
        </Card>
        <Card title={t("sections.title")} actions={<Button disabled>{t("sections.add")}</Button>}>
          <DataTable
            caption={t("sections.title")}
            captionHidden
            columns={sectionColumns}
            state={sections}
            rowKey={(row) => row.id}
            emptyTitle={t("sections.emptyTitle")}
            emptyBody={t("sections.emptyBody")}
          />
        </Card>
      </div>
    </div>
  );
}
