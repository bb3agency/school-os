"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console/DataTableV2";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button, ButtonLink, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import type { FilterChip } from "@/components/ui/FilterChips";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { useSchoolStructure, useSectionOptions } from "@/features/students/StudentList";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import {
  CATEGORIES,
  KEYS,
  MESSAGE_PURPOSES,
  MESSAGE_STATUSES,
  READ,
  RECORD,
  isUnavailable,
  sectionSlipsHref,
  type Category,
  type Message,
  type MessagePurpose,
  type MessageStatus,
} from "./data";
import { AutomaticCard, BroadcastsCard } from "./BroadcastScreens";
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
    <span className="flex flex-col items-start gap-1">
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
 * only as its last four digits; failure and skip reasons in words; the search is a POST body
 * (no personal data in URLs).
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
  const can = useStaffCan();
  const me = useStaffMe();
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const filtersId = useId();
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
  const rows: Loadable<readonly Message[]> =
    me === undefined
      ? { status: "loading" }
      : page.status === "ready"
        ? { status: "ready", data: page.data.items }
        : page;
  const nextCursor = page.status === "ready" ? (page.data.next_cursor ?? null) : null;

  const choosePurpose = (value: MessagePurpose | "") => {
    setCursor(null);
    setPurpose(value);
  };
  const chooseStatus = (value: MessageStatus | "") => {
    setCursor(null);
    setStatus(value);
  };
  const chips: FilterChip[] = [
    ...(purpose ? [{ id: "purpose", label: tl("purpose"), value: tl(`purposes.${purpose}`) }] : []),
    ...(status ? [{ id: "status", label: tl("status"), value: tl(`statuses.${status}`) }] : []),
  ];

  const columns: ColumnV2<Message>[] = [
    {
      key: "time",
      header: tl("columns.time"),
      className: "num whitespace-nowrap",
      cell: (item) => <Value>{formatDateTime(item.queued_at)}</Value>,
    },
    {
      key: "purpose",
      header: tl("columns.purpose"),
      cell: (item) => tl(`purposes.${item.purpose}`),
    },
    {
      key: "language",
      header: tl("columns.language"),
      cell: (item) => (
        <>
          {tl(`languages.${item.language}`)}
          {item.language_fallback ? (
            <span className="block text-sm text-ink-muted">{tl("fallback")}</span>
          ) : null}
        </>
      ),
    },
    {
      key: "phone",
      header: tl("columns.phone"),
      className: "num whitespace-nowrap",
      // Only the last four digits ever reach the browser (FR-MSG-017).
      cell: (item) => (
        <Value>{item.phone_last4 ? tl("phoneEnding", { last4: item.phone_last4 }) : null}</Value>
      ),
    },
    {
      key: "status",
      header: tl("columns.status"),
      cell: (item) => <StatusCell message={item} />,
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("logTitle")}
        description={t("logDescription")}
        actions={
          can(RECORD) ? (
            <ButtonLink href="/messages/slips" variant="secondary">
              {t("slips.open")}
            </ButtonLink>
          ) : null
        }
      />
      {me !== undefined && !allowed ? (
        <EmptyState icon="lock" title={t("noAccessTitle")} body={t("noAccessBody")} />
      ) : isUnavailable(page) ? (
        // 404 module_unavailable: WhatsApp is not offered here; say so, not "could not load".
        <MessagingGate data={page}>{() => null}</MessagingGate>
      ) : (
        <>
          {!studentId && !guardianId ? (
            <>
              <BroadcastsCard />
              <AutomaticCard />
            </>
          ) : null}
          <h2 className="text-lg font-semibold text-ink">{t("historyTitle")}</h2>
          <DataTableV2
            id="messages"
            caption={t("logTitle")}
            captionHidden
            columns={columns}
            state={rows}
            rowKey={(item) => item.id}
            stacked
            toolbar={
              <div
                role="search"
                aria-labelledby={filtersId}
                className="flex flex-wrap items-end gap-3"
              >
                <span id={filtersId} className="sr-only">
                  {tl("filtersLabel")}
                </span>
                <SelectField
                  label={tl("purpose")}
                  value={purpose}
                  onChange={(event) => choosePurpose(event.target.value as MessagePurpose | "")}
                  placeholder={tl("all")}
                  options={MESSAGE_PURPOSES.map((p) => ({ value: p, label: tl(`purposes.${p}`) }))}
                />
                <SelectField
                  label={tl("status")}
                  value={status}
                  onChange={(event) => chooseStatus(event.target.value as MessageStatus | "")}
                  placeholder={tl("all")}
                  options={MESSAGE_STATUSES.map((s) => ({ value: s, label: tl(`statuses.${s}`) }))}
                />
              </div>
            }
            chips={chips}
            onRemoveChip={(id) => (id === "purpose" ? choosePurpose("") : chooseStatus(""))}
            onClearFilters={() => {
              setCursor(null);
              setPurpose("");
              setStatus("");
            }}
            emptyTitle={tl("emptyTitle")}
            emptyBody={tl("emptyBody")}
            noResultsTitle={tl("noResultsTitle")}
            onRetry={() => void queryClient.invalidateQueries({ queryKey: KEYS.messages(filter) })}
            footer={
              nextCursor ? (
                <Button variant="secondary" size="sm" onClick={() => setCursor(nextCursor)}>
                  {tl("more")}
                </Button>
              ) : undefined
            }
          />
        </>
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
  const hintId = useId();
  const ready = sectionId !== "" && chosen.size > 0;
  return (
    <div className="space-y-5">
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
        <EmptyState icon="lock" title={tm("noAccessTitle")} body={tm("noAccessBody")} />
      ) : (
        <Card className="max-w-2xl">
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
                <div key={category} className="flex min-h-8 items-center gap-2">
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
            <p id={hintId} className="text-sm text-ink-muted">
              {t("hint")}
            </p>
            <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
              {ready ? (
                <a
                  href={sectionSlipsHref(sectionId, [...chosen])}
                  target="_blank"
                  rel="noopener"
                  aria-describedby={hintId}
                  className={buttonClasses("primary")}
                >
                  <Icon name="file" className="size-4" />
                  {t("print")}
                </a>
              ) : (
                <>
                  <Button disabled aria-disabled>
                    <Icon name="file" className="size-4" />
                    {t("print")}
                  </Button>
                  <p className="text-sm text-ink-muted">{t("chooseFirst")}</p>
                </>
              )}
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
