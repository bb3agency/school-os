"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { READ, type Circular, useCirculars } from "./data";
import { LoadGate, ReadingPill } from "./parts";

type Show = "open" | "all";

/** Still needs a person: not read yet, reading failed, or suggestions not decided / not reviewed. */
export function needsAttention(circular: Circular): boolean {
  return !circular.reviewed;
}

/**
 * Circulars inbox (US-1601, US-1602): every circular the user can see, where its AI reading
 * stands and what is left to decide. Opening one shows the summary with source chips and the
 * suggested deadlines to confirm or dismiss.
 */
export function CircularsScreen() {
  const t = useTranslations("circulars");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(READ);
  const [show, setShow] = useState<Show>("open");
  const list = useCirculars(undefined, allowed);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
        actions={
          <Link href="/documents/new" className={buttonClasses("secondary")}>
            {t("upload")}
          </Link>
        }
      />
      {meLoaded && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <LoadGate data={meLoaded ? list : { status: "loading" }}>
          {(page) => {
            const rows = show === "open" ? page.data.filter(needsAttention) : page.data;
            return (
              <Card
                title={t("inboxTitle")}
                actions={
                  <SegmentedControl
                    legend={t("showLabel")}
                    size="sm"
                    value={show}
                    onValueChange={(value) => setShow(value as Show)}
                    options={[
                      { value: "open", label: t("showOpen") },
                      { value: "all", label: t("showAll") },
                    ]}
                  />
                }
              >
                {rows.length === 0 ? (
                  <EmptyState
                    icon="inbox"
                    title={show === "open" ? t("emptyOpenTitle") : t("emptyTitle")}
                    body={t("emptyBody")}
                  />
                ) : (
                  <TableScroll label={t("inboxTitle")}>
                    <Table>
                      <THead>
                        <Tr>
                          <Th>{t("columns.circular")}</Th>
                          <Th>{t("columns.issuedOn")}</Th>
                          <Th>{t("columns.reading")}</Th>
                          <Th>{t("columns.toDecide")}</Th>
                          <Th>{t("columns.tasks")}</Th>
                        </Tr>
                      </THead>
                      <TBody>
                        {rows.map((row) => (
                          <Tr key={row.document_id}>
                            <Td>
                              <Link
                                href={`/circulars/${row.document_id}`}
                                className="font-semibold text-primary underline underline-offset-4 hover:no-underline"
                              >
                                {row.title}
                              </Link>
                              {row.issuer ? (
                                <p className="text-xs text-ink-muted">{row.issuer}</p>
                              ) : null}
                            </Td>
                            <Td>
                              <Value>{formatDate(row.issued_on)}</Value>
                            </Td>
                            <Td>
                              <span className="flex flex-wrap gap-1">
                                <ReadingPill status={row.reading_status} />
                                {row.reviewed ? (
                                  <Pill variant="positive">{t("reviewed")}</Pill>
                                ) : null}
                              </span>
                            </Td>
                            <Td>{row.open_suggestions}</Td>
                            <Td>{row.tasks}</Td>
                          </Tr>
                        ))}
                      </TBody>
                    </Table>
                  </TableScroll>
                )}
              </Card>
            );
          }}
        </LoadGate>
      )}
    </div>
  );
}
