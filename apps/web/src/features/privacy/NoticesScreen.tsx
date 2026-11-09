"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, ButtonLink, buttonClasses } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { todayInIndia } from "@/features/change-requests/dates";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { typedDateToIso, useDateInput } from "@/lib/date-format";
import { formatDate, formatDateTime } from "@/lib/format";
import * as z from "@/lib/zod";
import { Link } from "@/i18n/navigation";
import { NoticePdfButton } from "./parts";
import { WHATSAPP_PERMS } from "./WhatsAppNoticeScreens";
import {
  NOTICE_CHANNELS,
  PRIVACY_KEYS,
  PRIVACY_MANAGE,
  PRIVACY_READ,
  templatePreviewHref,
  type PrivacyFile,
  type PrivacyNotice,
  type PrivacyTemplate,
  type TemplateKey,
} from "./types";

const TEMPLATE_KEYS: readonly TemplateKey[] = ["notice_parents", "notice_staff"];

/**
 * FP-13: a notice record sent on WhatsApp carries its broadcast. LEAD: after `make openapi`
 * `PrivacyNoticeOut` has `broadcast_id`; read it directly then.
 */
function hasBroadcast(row: PrivacyNotice): boolean {
  const value = (row as { broadcast_id?: unknown }).broadcast_id;
  return typeof value === "string" && value.length > 0;
}

/** The "Record that we issued it" form; the API checks the audience and version again. */
export function buildNoticeSchema(today = todayInIndia()) {
  return z
    .object({
      template_key: z.enum(TEMPLATE_KEYS, { error: "chooseOption" }),
      template_version: z
        .string()
        .trim()
        .min(1, { error: "required" })
        .max(40, { error: "tooLong" }),
      language: z.enum(["en", "te"], { error: "chooseOption" }).default("en"),
      issued_on: z.string().trim().min(1, { error: "required" }),
      channel: z.enum(NOTICE_CHANNELS, { error: "chooseOption" }),
      // D60: also make the notice's PDF once it is recorded (checkbox: "on" when ticked).
      make_pdf: z
        .string()
        .optional()
        .transform((value) => value === "on"),
    })
    .superRefine((data, context) => {
      const iso = typedDateToIso(data.issued_on);
      if (!iso) context.addIssue({ code: "custom", path: ["issued_on"], message: "invalidDate" });
      else if (iso > today) {
        context.addIssue({ code: "custom", path: ["issued_on"], message: "invalidDate" });
      }
    });
}

/**
 * Privacy notices (US-2608; FR-DPDP-001..004): the parents' and staff notice templates with
 * their version, a preview (print view) and a PDF; "Record that we issued it"; the issue
 * history. Not legal advice: the school decides what it sends.
 */
export function NoticesScreen() {
  const t = useTranslations("privacy.notices");
  const ta = useTranslations("privacy.audience");
  const tch = useTranslations("privacy.channels");
  const tl = useTranslations("language");
  const tn = useTranslations("school.nav");
  const tw = useTranslations("privacy.whatsapp");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const mePending = useStaffMeQuery().isPending;
  const readable = can(PRIVACY_READ);
  const [recording, setRecording] = useState(false);

  const templates = useQuery({
    queryKey: PRIVACY_KEYS.templates,
    queryFn: () => unwrap(api.GET("/api/v1/privacy/templates")),
    enabled: readable,
    retry: false,
  });
  const notices = useQuery({
    queryKey: PRIVACY_KEYS.notices,
    queryFn: () => unwrap(api.GET("/api/v1/privacy/notices")),
    enabled: readable,
    retry: false,
  });

  const manage = can(PRIVACY_MANAGE);
  // FP-13: "Send on WhatsApp" also needs parent notice approval and message sending.
  const canWhatsApp = WHATSAPP_PERMS.every((permission) => can(permission));
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        manage && templates.data ? (
          <span className="flex flex-wrap gap-2">
            {canWhatsApp ? (
              <ButtonLink href="/privacy/notices/whatsapp" variant="secondary">
                {tw("open")}
              </ButtonLink>
            ) : null}
            <Button onClick={() => setRecording(true)}>
              <Icon name="plus" className="size-4" />
              {t("record")}
            </Button>
          </span>
        ) : null
      }
    />
  );

  if (!mePending && !readable) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  const list = templates.data ?? [];
  const newer = list.filter((template) => template.newer_version_available);
  const unfilled = [...new Set(list.flatMap((template) => template.unfilled))];

  const templateColumns: ColumnV2<PrivacyTemplate>[] = [
    { key: "audience", stack: "title", header: t("colNotice"), cell: (row) => ta(row.audience) },
    {
      key: "version",
      header: t("colVersion"),
      cell: (row) => <span className="num">{row.template_version}</span>,
    },
    {
      key: "issued",
      header: t("colLastIssued"),
      cell: (row) => (
        <span className="num">
          <Value>{row.last_issued_version}</Value>
        </span>
      ),
    },
    {
      key: "actions",
      stack: "actions",
      header: t("colActions"),
      cell: (row) => (
        <span className="flex flex-wrap items-start gap-2">
          <a
            href={templatePreviewHref(row.key)}
            target="_blank"
            rel="noopener noreferrer"
            className={buttonClasses("secondary", "sm")}
          >
            {t("preview")}
            <span className="sr-only">
              : {ta(row.audience)} ({t("newTab")})
            </span>
          </a>
          <NoticePdfButton templateKey={row.key} label={ta(row.audience)} />
        </span>
      ),
    },
  ];

  const historyColumns: ColumnV2<PrivacyNotice>[] = [
    {
      key: "issued",
      stack: "title",
      header: t("colIssuedOn"),
      cell: (row) => <span className="num">{formatDate(row.issued_on)}</span>,
    },
    { key: "audience", header: t("colAudience"), cell: (row) => ta(row.audience) },
    {
      key: "version",
      header: t("colVersion"),
      cell: (row) => <span className="num">{row.template_version}</span>,
    },
    { key: "language", header: t("colLanguage"), cell: (row) => tl(row.language) },
    {
      key: "channel",
      header: t("colChannel"),
      cell: (row) =>
        // FP-13: a notice sent on WhatsApp from SchoolOS links to who it reached.
        hasBroadcast(row) ? (
          <Link
            href={`/privacy/notices/${row.id}/delivery`}
            className="font-semibold text-primary underline"
          >
            {tw("channel")}
            <span className="sr-only">: {tw("delivery.title")}</span>
          </Link>
        ) : (
          tch(row.channel)
        ),
    },
    {
      key: "by",
      header: t("colRecordedBy"),
      cell: (row) => (
        <span className="flex flex-col">
          <Value>{row.recorded_by_name}</Value>
          <span className="num text-xs text-ink-muted">{formatDateTime(row.recorded_at)}</span>
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      {header}
      <p className="text-sm text-ink-muted">{t("notLegalAdvice")}</p>
      {newer.length > 0 ? (
        <Alert tone="info" title={t("newerTitle")}>
          {t("newerBody", { notices: newer.map((row) => ta(row.audience)).join(", ") })}
        </Alert>
      ) : null}
      {unfilled.length > 0 ? (
        <Alert tone="warning" title={t("unfilledTitle")}>
          {t("unfilledBody")}
        </Alert>
      ) : null}
      <section aria-labelledby="privacy-templates" className="space-y-3">
        <h2 id="privacy-templates" className="text-lg font-semibold text-ink">
          {t("templatesTitle")}
        </h2>
        <DataTableV2
          id="privacy-templates-table"
          stacked
          caption={t("templatesTitle")}
          captionHidden
          columns={templateColumns}
          state={toLoadable({ ...templates, data: list })}
          rowKey={(row) => row.key}
          emptyTitle={t("noTemplatesTitle")}
          emptyBody={t("noTemplatesBody")}
          onRetry={() => void templates.refetch()}
        />
      </section>
      <section aria-labelledby="privacy-notice-history" className="space-y-3">
        <h2 id="privacy-notice-history" className="text-lg font-semibold text-ink">
          {t("historyTitle")}
        </h2>
        <DataTableV2
          id="privacy-notice-history-table"
          stacked
          caption={t("historyTitle")}
          captionHidden
          columns={historyColumns}
          state={toLoadable({ ...notices, data: notices.data ?? [] })}
          rowKey={(row) => row.id}
          emptyTitle={t("historyEmptyTitle")}
          emptyBody={t("historyEmptyBody")}
          onRetry={() => void notices.refetch()}
        />
      </section>
      {manage && templates.data ? (
        <RecordNoticeDialog open={recording} onOpenChange={setRecording} templates={list} />
      ) : null}
    </div>
  );
}

/** What the dialog's submit gives back: the notice, and the PDF when it was asked for. */
export interface RecordedNotice {
  notice: PrivacyNotice;
  pdfAsked: boolean;
  file: PrivacyFile | null;
  pdfError: unknown;
}

/**
 * Records the notice, then (when ticked) asks for its PDF. The notice stands even when the
 * PDF request fails: the error is kept for the result view, which offers "Make the PDF".
 */
export async function recordNotice(
  record: () => Promise<PrivacyNotice>,
  makePdf: (() => Promise<PrivacyFile>) | null,
): Promise<RecordedNotice> {
  const notice = await record();
  if (!makePdf) return { notice, pdfAsked: false, file: null, pdfError: undefined };
  try {
    return { notice, pdfAsked: true, file: await makePdf(), pdfError: undefined };
  } catch (failure) {
    return { notice, pdfAsked: true, file: null, pdfError: failure };
  }
}

function RecordNoticeDialog({
  open,
  onOpenChange,
  templates,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  templates: readonly PrivacyTemplate[];
}) {
  const t = useTranslations("privacy.notices");
  const ta = useTranslations("privacy.audience");
  const tch = useTranslations("privacy.channels");
  const tl = useTranslations("language");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const dates = useDateInput();
  const telugu = useTeluguEnabled();
  const base = useId();
  const [key, setKey] = useState<TemplateKey>(templates[0]?.key ?? "notice_parents");
  const version = templates.find((row) => row.key === key)?.template_version ?? "";
  return (
    <ActionDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("recordTitle")}
      description={t("recordBody")}
      confirmLabel={t("recordConfirm")}
      schema={buildNoticeSchema()}
      extra={() => (telugu ? {} : { language: "en" })}
      invalidate={[PRIVACY_KEYS.notices, PRIVACY_KEYS.templates]}
      errorNamespace="privacy"
      submit={(input, idempotencyKey) => {
        const audience = input.template_key === "notice_staff" ? "staff" : "parents";
        return recordNotice(
          () =>
            unwrap(
              api.POST("/api/v1/privacy/notices", {
                headers: { "Idempotency-Key": idempotencyKey },
                body: {
                  template_key: input.template_key,
                  template_version: input.template_version,
                  audience,
                  language: input.language,
                  issued_on: typedDateToIso(input.issued_on) ?? input.issued_on,
                  channel: input.channel,
                },
              }),
            ),
          input.make_pdf
            ? () =>
                unwrap(
                  api.POST("/api/v1/privacy/templates/{key}/pdf", {
                    params: { path: { key: input.template_key } },
                  }),
                )
            : null,
        );
      }}
      renderResult={(result, close) => (
        <div className="space-y-4">
          <Alert tone="success" live title={t("recordedTitle")}>
            {result.pdfAsked ? t("recordedPdfBody") : t("recordedBody")}
          </Alert>
          {result.pdfAsked ? (
            <div className="space-y-2">
              <ApiErrorAlert error={result.pdfError} namespace="privacy" />
              <NoticePdfButton
                templateKey={result.notice.template_key}
                label={ta(result.notice.audience)}
                initialFile={result.file}
              />
            </div>
          ) : null}
          <div className="flex justify-end">
            <Button variant="secondary" onClick={close}>
              {tc("close")}
            </Button>
          </div>
        </div>
      )}
    >
      {(errors) => (
        <div className="space-y-4">
          <SelectField
            id={`${base}-template`}
            name="template_key"
            label={t("fieldNotice")}
            error={errors.template_key}
            value={key}
            onChange={(event) => {
              const next = TEMPLATE_KEYS.find((item) => item === event.currentTarget.value);
              if (next) setKey(next);
            }}
            options={templates.map((row) => ({ value: row.key, label: ta(row.audience) }))}
          />
          <TextField
            key={key}
            id={`${base}-version`}
            name="template_version"
            label={t("fieldVersion")}
            hint={t("fieldVersionHint")}
            error={errors.template_version}
            defaultValue={version}
            maxLength={40}
            autoComplete="off"
            className="num"
          />
          {telugu ? (
            <SelectField
              id={`${base}-language`}
              name="language"
              label={t("fieldLanguage")}
              error={errors.language}
              defaultValue="en"
              options={(["en", "te"] as const).map((value) => ({ value, label: tl(value) }))}
            />
          ) : null}
          <TextField
            id={`${base}-issued`}
            name="issued_on"
            label={t("fieldIssuedOn")}
            hint={tc("dateHintFormat", dates.hint("2026-10-21"))}
            error={errors.issued_on}
            defaultValue={dates.fromIso(todayInIndia())}
            inputMode="numeric"
            placeholder={dates.placeholder}
            autoComplete="off"
            className="num"
          />
          <SelectField
            id={`${base}-channel`}
            name="channel"
            label={t("fieldChannel")}
            error={errors.channel}
            defaultValue=""
            placeholder={tc("chooseOne")}
            options={NOTICE_CHANNELS.map((value) => ({ value, label: tch(value) }))}
          />
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" name="make_pdf" className="mt-1 size-4 accent-primary" />
            <span>
              {t("makePdf")}
              <span className="block text-ink-muted">{t("makePdfHint")}</span>
            </span>
          </label>
        </div>
      )}
    </ActionDialog>
  );
}
