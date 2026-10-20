"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatCount, formatInr } from "@/lib/format";
import {
  SEND,
  isUnavailable,
  useMessagingSettings,
  type FeeReminderPreview,
  type FeeReminderSelection,
  type FeeRemindersSent,
} from "./data";

/** The reason codes a reminder preview can list, in the order they are shown. */
const ACCOUNT_REASONS = ["nothing_due", "below_minimum", "no_guardian"] as const;
const GUARDIAN_REASONS = [
  "no_optin",
  "opted_out",
  "optin_for_other_number",
  "no_mobile_number",
  "already_sent",
] as const;

const NO_FIELDS = z.object({});

/**
 * Whether this person may send fee reminders from the dues list here: `message.send` (the
 * dues list itself needs `finance.read`) and WhatsApp offered on this deployment (the settings
 * read answers 404 otherwise). The API checks both again.
 */
export function useCanSendFeeReminders(): boolean {
  const can = useStaffCan();
  const allowed = can(SEND);
  const settings = useMessagingSettings(allowed);
  return allowed && settings.status === "ready" && !isUnavailable(settings);
}

function Preview({ preview }: { preview: FeeReminderPreview }) {
  const t = useTranslations("messaging.feeReminders");
  const ts = useTranslations("messaging.send");
  const tr = useTranslations("messaging.log.reasons");
  const locale = useLocale() as Locale;
  const count = (n: number) => formatCount(n, locale) ?? "0";
  const accounts = ACCOUNT_REASONS.filter((code) => (preview.skipped_accounts[code] ?? 0) > 0);
  const guardians = GUARDIAN_REASONS.filter((code) => (preview.skipped_guardians[code] ?? 0) > 0);
  const blocked = preview.blocked_reason;
  return (
    <section aria-label={t("previewLabel")} className="space-y-4">
      <dl className="grid gap-3 sm:grid-cols-3">
        <div>
          <dt className="text-sm text-ink-muted">{t("recipients")}</dt>
          <dd className="num text-2xl font-semibold text-ink">
            <Value>{count(preview.recipients)}</Value>
          </dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("guardians")}</dt>
          <dd className="num text-2xl font-semibold text-ink">
            <Value>{count(preview.guardians)}</Value>
          </dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("cost")}</dt>
          <dd className="num text-2xl font-semibold text-ink">
            <Value>{formatInr(preview.estimated_cost_inr, locale)}</Value>
          </dd>
        </div>
      </dl>
      <p className="text-sm text-ink-muted">{ts("costNote")}</p>
      {accounts.length > 0 ? (
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-ink">{t("skippedStudents")}</h3>
          <ul className="space-y-1 text-sm text-ink">
            {accounts.map((code) => (
              <li key={code} className="flex justify-between gap-4">
                <span>{t(`accountReasons.${code}`)}</span>
                <span className="num">{count(preview.skipped_accounts[code] ?? 0)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {guardians.length > 0 ? (
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-ink">{t("skippedGuardians")}</h3>
          <ul className="space-y-1 text-sm text-ink">
            {guardians.map((code) => (
              <li key={code} className="flex justify-between gap-4">
                <span>{code === "already_sent" ? t("alreadySent") : tr(code)}</span>
                <span className="num">{count(preview.skipped_guardians[code] ?? 0)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {blocked ? (
        <Alert tone="warning" title={ts("blockedTitle")}>
          {tr.has(blocked as "no_optin") ? tr(blocked as "no_optin") : ts("blockedOther")}{" "}
          <Link href="/settings/whatsapp" className="underline underline-offset-4">
            {ts("openSettings")}
          </Link>
        </Alert>
      ) : preview.recipients === 0 ? (
        <p className="text-sm text-ink-muted">{t("nobody")}</p>
      ) : null}
    </section>
  );
}

/**
 * "Send reminder" from the dues list (FR-MSG-025; docs/17 §5.8): opened for the rows chosen on
 * the visible page, or for everyone matching the list's filter. It asks the API who would get a
 * reminder (no state is kept on the server), shows the counts, who is left out and why and the
 * estimated Meta cost, then sends with the consequence line. One Idempotency-Key per dialog.
 */
export function FeeRemindersDialog({
  selection,
  open,
  onOpenChange,
}: {
  selection: FeeReminderSelection | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("messaging.feeReminders");
  const api = useBffClient("staff");
  // The answer belongs to the selection it was asked for (each opening passes a new one).
  const [answer, setAnswer] = useState<{
    for: FeeReminderSelection;
    preview: FeeReminderPreview | null;
    error: unknown;
  } | null>(null);

  useEffect(() => {
    if (!open || !selection) return;
    let current = true;
    unwrap(api.POST("/api/v1/messaging/fee-reminders/preview", { body: selection }))
      .then((out) => {
        if (current) setAnswer({ for: selection, preview: out, error: undefined });
      })
      .catch((failure: unknown) => {
        if (current) setAnswer({ for: selection, preview: null, error: failure });
      });
    return () => {
      current = false;
    };
  }, [api, open, selection]);

  const mine = answer !== null && answer.for === selection ? answer : null;
  const preview = mine?.preview ?? null;
  const error = mine?.error;

  const sendable = preview !== null && !preview.blocked_reason && preview.recipients > 0;
  const chosen = selection?.account_ids?.length ?? 0;

  return (
    <ActionDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("title")}
      description={
        selection?.all_matching ? t("descriptionAll") : t("descriptionRows", { count: chosen })
      }
      confirmLabel={t("confirm", { count: preview?.recipients ?? 0 })}
      confirmDisabled={!sendable}
      consequence={sendable ? t("consequence") : undefined}
      schema={NO_FIELDS}
      errorNamespace="messaging"
      submit={(_, idempotencyKey) => {
        if (!selection || !preview) throw new Error("no preview");
        return unwrap(
          api.POST("/api/v1/messaging/fee-reminders", {
            headers: { "Idempotency-Key": idempotencyKey },
            body: { ...selection, preview_token: preview.preview_token },
          }),
        );
      }}
      renderResult={(result: FeeRemindersSent, close) => (
        <>
          <Alert tone="success" live>
            {t("queued", { count: result.queued })}
          </Alert>
          <div className="flex justify-end">
            <Button onClick={close}>{t("done")}</Button>
          </div>
        </>
      )}
    >
      {() => (
        <>
          <ApiErrorAlert error={error} namespace="messaging" />
          {preview ? (
            <Preview preview={preview} />
          ) : error === undefined ? (
            <p role="status" className="text-sm text-ink-muted">
              {t("checking")}
            </p>
          ) : null}
        </>
      )}
    </ActionDialog>
  );
}
