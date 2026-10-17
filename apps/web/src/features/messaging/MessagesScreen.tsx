"use client";

import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { useSchoolStructure, useSectionOptions } from "@/features/students/StudentList";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import {
  CATEGORIES,
  KEYS,
  MESSAGE_PURPOSES,
  MESSAGE_STATUSES,
  READ,
  RECORD,
  sectionSlipsHref,
  type Category,
  type Message,
  type MessagePurpose,
  type MessageStatus,
} from "./data";
import { MessagingGate } from "./WhatsAppSettingsScreen";

const STATUS_PILL: Record<MessageStatus, PillVariant> = {
  queued: "progress",
  sent: "progress",
  delivered: "done",
  read: "done",
  failed: "negative",
  skipped: "sample",
};

function StatusCell({ message }: { message: Message }) {
  const t = useTranslations("messaging.log");
  const why =
    message.status === "skipped"
      ? message.skip_reason
      : message.status === "failed"
        ? message.error_reason
        : null;
  return (
    <span className="flex flex-col gap-1">
      <Pill variant={STATUS_PILL[message.status]}>{t(`statuses.${message.status}`)}</Pill>
      {why ? (
        <span className="text-sm text-ink-muted">
          {t.has(`reasons.${why}` as "reasons.no_optin")
            ? t(`reasons.${why}` as "reasons.no_optin")
            : t("reasons.other")}
        </span>
      ) : null}
    </span>
  );
}

/**
 * Message history and delivery log (US-2208; FR-MSG-016, FR-MSG-017): newest first, filtered by
 * purpose and status (and by student or guardian when opened from their page); a phone shows
 * only as its last four digits; the search is a POST body (no personal data in URLs).
 */
export function MessagesScreen({
  studentId,
  guardianId,
}: {
  studentId?: string | undefined;
  guardianId?: string | undefined;
}) {
  const t = useTranslations("messaging");
  const tl = useTranslations("messaging.log");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const api = useBffClient("staff");
  const purposeId = useId();
  const [purpose, setPurpose] = useState<MessagePurpose | "">("");
  const [status, setStatus] = useState<MessageStatus | "">("");
  const [cursor, setCursor] = useState<string | null>(null);
  const filter = JSON.stringify({ purpose, status, studentId, guardianId, cursor });
  const allowed = can(READ);
  const page = useApiQuery(
    KEYS.messages(filter),
    () =>
      unwrap(
        api.POST("/api/v1/messages/search", {
          body: {
            ...(purpose ? { purpose } : {}),
            ...(status ? { status } : {}),
            ...(studentId ? { student_id: studentId } : {}),
            ...(guardianId ? { guardian_id: guardianId } : {}),
            ...(cursor ? { cursor } : {}),
            limit: 50,
          },
        }),
      ),
    { enabled: me !== undefined && allowed },
  );
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("logTitle")}
        description={t("logDescription")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("logTitle") }]}
        actions={
          can(RECORD) ? (
            <ButtonLink href="/messages/slips" variant="secondary">
              {t("slips.open")}
            </ButtonLink>
          ) : null
        }
      />
      {me !== undefined && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <Card>
          <div className="mb-4 grid gap-4 sm:grid-cols-2" role="search" aria-labelledby={purposeId}>
            <SelectField
              id={purposeId}
              label={tl("purpose")}
              value={purpose}
              onChange={(event) => {
                setCursor(null);
                setPurpose(event.target.value as MessagePurpose | "");
              }}
              placeholder={tl("all")}
              options={MESSAGE_PURPOSES.map((p) => ({ value: p, label: tl(`purposes.${p}`) }))}
            />
            <SelectField
              label={tl("status")}
              value={status}
              onChange={(event) => {
                setCursor(null);
                setStatus(event.target.value as MessageStatus | "");
              }}
              placeholder={tl("all")}
              options={MESSAGE_STATUSES.map((s) => ({ value: s, label: tl(`statuses.${s}`) }))}
            />
          </div>
          <MessagingGate data={me === undefined ? { status: "loading" } : page}>
            {(current) =>
              current.items.length === 0 ? (
                <EmptyState icon="inbox" title={tl("emptyTitle")} body={tl("emptyBody")} />
              ) : (
                <div className="space-y-3">
                  <TableScroll label={t("logTitle")}>
                    <Table>
                      <THead>
                        <Tr>
                          <Th>{tl("columns.time")}</Th>
                          <Th>{tl("columns.purpose")}</Th>
                          <Th>{tl("columns.language")}</Th>
                          <Th>{tl("columns.phone")}</Th>
                          <Th>{tl("columns.status")}</Th>
                        </Tr>
                      </THead>
                      <TBody>
                        {current.items.map((item) => (
                          <Tr key={item.id}>
                            <Td>
                              <Value>{formatDateTime(item.queued_at)}</Value>
                            </Td>
                            <Td>{tl(`purposes.${item.purpose}`)}</Td>
                            <Td>
                              {tl(`languages.${item.language}`)}
                              {item.language_fallback ? (
                                <span className="block text-sm text-ink-muted">
                                  {tl("fallback")}
                                </span>
                              ) : null}
                            </Td>
                            <Td>
                              <Value>
                                {item.phone_last4
                                  ? tl("phoneEnding", { last4: item.phone_last4 })
                                  : null}
                              </Value>
                            </Td>
                            <Td>
                              <StatusCell message={item} />
                            </Td>
                          </Tr>
                        ))}
                      </TBody>
                    </Table>
                  </TableScroll>
                  {current.next_cursor ? (
                    <Button
                      variant="secondary"
                      onClick={() => setCursor(current.next_cursor ?? null)}
                    >
                      {tl("more")}
                    </Button>
                  ) : null}
                </div>
              )
            }
          </MessagingGate>
        </Card>
      )}
    </div>
  );
}

/**
 * Printable join slips for a section (US-2202 AC2; FR-MSG-009): each guardian with a mobile
 * number gets a slip in their parent language with a single-use code and the school's QR. The
 * print view opens through the BFF in a new tab (A4, two slips a page, the API's strict CSP).
 */
export function JoinSlipsScreen() {
  const t = useTranslations("messaging.slips");
  const tm = useTranslations("messaging");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const structure = useSchoolStructure();
  const sections = useSectionOptions(structure);
  const [sectionId, setSectionId] = useState("");
  const [chosen, setChosen] = useState<Set<Category>>(() => new Set(CATEGORIES));
  const allowed = can(RECORD);
  const legendId = useId();
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: tm("logTitle"), href: "/messages" },
          { label: t("title") },
        ]}
      />
      {me !== undefined && !allowed ? (
        <Alert tone="info" title={tm("noAccessTitle")}>
          {tm("noAccessBody")}
        </Alert>
      ) : (
        <Card>
          <div className="space-y-4">
            <SelectField
              label={t("section")}
              value={sectionId}
              onChange={(event) => setSectionId(event.target.value)}
              placeholder={t("chooseSection")}
              options={sections}
            />
            <fieldset aria-labelledby={legendId} className="space-y-2">
              <legend id={legendId} className="font-semibold text-ink">
                {t("categories")}
              </legend>
              {CATEGORIES.map((category) => (
                <div key={category} className="flex items-center gap-2">
                  <input
                    id={`slip-${category}`}
                    type="checkbox"
                    className="size-5"
                    checked={chosen.has(category)}
                    onChange={(event) =>
                      setChosen((current) => {
                        const next = new Set(current);
                        if (event.target.checked) next.add(category);
                        else next.delete(category);
                        return next;
                      })
                    }
                  />
                  <label htmlFor={`slip-${category}`}>{t(`category.${category}`)}</label>
                </div>
              ))}
            </fieldset>
            <p className="text-sm text-ink-muted">{t("hint")}</p>
            {sectionId && chosen.size > 0 ? (
              <a
                href={sectionSlipsHref(sectionId, [...chosen])}
                target="_blank"
                rel="noopener"
                className="inline-flex font-semibold text-primary underline underline-offset-4"
              >
                {t("print")}
              </a>
            ) : (
              <p className="text-sm text-ink-muted">{t("chooseFirst")}</p>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
