"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useMemo, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField } from "@/components/ui/Input";
import { Value } from "@/components/ui/Value";
import { useSchoolStructure, useSectionOptions } from "@/features/students/StudentList";
import type { Locale } from "@/i18n/routing";
import { Link } from "@/i18n/navigation";
import { newIdempotencyKey, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatCount, formatDateTime, formatInr } from "@/lib/format";
import {
  KEYS,
  LIVE_REFRESH_MS,
  SEND,
  deliveredCount,
  isUnavailable,
  useMessagingSettings,
  type Broadcast,
  type BroadcastPreview,
} from "./data";

/** The reason codes a preview can list, in the order they are shown. */
const SKIP_ORDER = [
  "no_optin",
  "opted_out",
  "optin_for_other_number",
  "no_mobile_number",
  "already_sent",
] as const;

export function DeliveryProgress({ broadcast }: { broadcast: Broadcast }) {
  const t = useTranslations("messaging.broadcasts");
  const locale = useLocale() as Locale;
  const labelId = useId();
  const counts = broadcast.counts;
  const delivered = deliveredCount(counts);
  const total = Math.max(broadcast.queued, 1);
  const count = (n: number) => formatCount(n, locale) ?? "0";
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap justify-between gap-2 text-sm">
        <span id={labelId} className="font-semibold">
          {t("deliveredOf", { delivered: count(delivered), total: count(broadcast.queued) })}
        </span>
        <span className="text-ink-muted">
          {broadcast.status === "done" ? t("finished") : t("stillSending")}
        </span>
      </div>
      <progress aria-labelledby={labelId} className="w-full" max={total} value={delivered}>
        {count(delivered)}
      </progress>
      <p className="text-sm text-ink-muted">
        {t("countsLine", {
          waiting: count(counts.queued ?? 0),
          sent: count(counts.sent ?? 0),
          delivered: count(counts.delivered ?? 0),
          read: count(counts.read ?? 0),
          failed: count((counts.failed ?? 0) + (counts.skipped ?? 0)),
        })}
      </p>
    </div>
  );
}

/** The live counts of one broadcast, asked again every few seconds while it is sending. */
function LiveBroadcast({ id }: { id: string }) {
  const t = useTranslations("messaging.broadcasts");
  const api = useBffClient("staff");
  const report = useApiQuery(
    KEYS.broadcast(id),
    () =>
      unwrap(
        api.GET("/api/v1/messaging/broadcasts/{broadcast_id}", {
          params: { path: { broadcast_id: id } },
        }),
      ),
    {
      refetchInterval: (data) => (data?.broadcast.status === "done" ? false : LIVE_REFRESH_MS),
    },
  );
  if (report.status !== "ready") return null;
  return (
    <div className="space-y-3" role="status" aria-live="polite">
      <DeliveryProgress broadcast={report.data.broadcast} />
      <Link
        href={`/messages/broadcasts/${id}`}
        className="text-sm text-primary underline underline-offset-4"
      >
        {t("openReport")}
      </Link>
    </div>
  );
}

function PreviewCard({
  preview,
  resend,
  pending,
  onResend,
  onConfirm,
}: {
  preview: BroadcastPreview;
  resend: boolean;
  pending: boolean;
  onResend: (on: boolean) => void;
  onConfirm: (reason: string | null) => void;
}) {
  const t = useTranslations("messaging.send");
  const tr = useTranslations("messaging.log.reasons");
  const tl = useTranslations("messaging.log.languages");
  const locale = useLocale() as Locale;
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const consequenceId = useId();
  const count = (n: number) => formatCount(n, locale) ?? "0";
  const skipped = SKIP_ORDER.filter((code) => (preview.skipped[code] ?? 0) > 0);
  const blocked = preview.blocked_reason;
  const languages = Object.entries(preview.languages).filter(([, n]) => n > 0);

  function confirm() {
    if (resend) {
      const clean = reason.trim();
      if (clean.length < 10) {
        setReasonError(t("reasonTooShort"));
        return;
      }
      setReasonError(undefined);
      onConfirm(clean);
      return;
    }
    onConfirm(null);
  }

  return (
    <section
      aria-label={t("previewLabel")}
      className="space-y-4 rounded-lg border border-border p-4"
    >
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
      <p className="text-sm text-ink-muted">{t("costNote")}</p>
      {languages.length > 0 ? (
        <p className="text-sm text-ink">
          {t("languagesLine", {
            languages: languages.map(([code, n]) => `${tl(code as "en")} ${count(n)}`).join(", "),
          })}
          {preview.language_fallbacks > 0
            ? " " + t("fallbacks", { count: count(preview.language_fallbacks) })
            : null}
        </p>
      ) : null}
      {skipped.length > 0 ? (
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-ink">{t("skippedTitle")}</h3>
          <ul className="space-y-1 text-sm text-ink">
            {skipped.map((code) => (
              <li key={code} className="flex justify-between gap-4">
                <span>{tr(code)}</span>
                <span className="num">{count(preview.skipped[code] ?? 0)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {blocked ? (
        <Alert tone="warning" title={t("blockedTitle")}>
          {tr.has(blocked as "no_optin") ? tr(blocked as "no_optin") : t("blockedOther")}{" "}
          <Link href="/settings/whatsapp" className="underline underline-offset-4">
            {t("openSettings")}
          </Link>
        </Alert>
      ) : null}
      {preview.already_sent > 0 || resend ? (
        <div className="flex min-h-8 items-center gap-2">
          <input
            id={`resend-${preview.notice_id}`}
            type="checkbox"
            className="size-5"
            checked={resend}
            onChange={(event) => onResend(event.target.checked)}
          />
          <label htmlFor={`resend-${preview.notice_id}`}>
            {t("resend", { count: count(preview.already_sent) })}
          </label>
        </div>
      ) : null}
      {resend ? (
        <TextAreaField
          label={t("reason")}
          hint={t("reasonHint")}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          error={reasonError}
          maxLength={300}
          rows={2}
        />
      ) : null}
      {!blocked && preview.recipients > 0 ? (
        <div className="space-y-2 border-t border-border pt-4">
          <p id={consequenceId} className="text-sm text-ink">
            {t("consequence")}
          </p>
          <Button
            variant="primary"
            disabled={pending}
            aria-disabled={pending || undefined}
            aria-describedby={consequenceId}
            onClick={confirm}
          >
            {t("confirm", { count: count(preview.recipients) })}
          </Button>
        </div>
      ) : !blocked ? (
        <p className="text-sm text-ink-muted">{t("nobody")}</p>
      ) : null}
    </section>
  );
}

function PastBroadcasts({ noticeId }: { noticeId: string }) {
  const t = useTranslations("messaging.broadcasts");
  const api = useBffClient("staff");
  const page = useApiQuery(KEYS.noticeBroadcasts(noticeId), () =>
    unwrap(
      api.GET("/api/v1/notices/{notice_id}/broadcasts", {
        params: { path: { notice_id: noticeId }, query: { limit: 20 } },
      }),
    ),
  );
  if (page.status !== "ready" || page.data.items.length === 0) return null;
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-ink">{t("earlier")}</h3>
      <ul className="space-y-3">
        {page.data.items.map((item) => (
          <li key={item.id} className="space-y-1">
            <Link
              href={`/messages/broadcasts/${item.id}`}
              className="text-sm text-primary underline underline-offset-4"
            >
              {t("sentOn", { when: formatDateTime(item.confirmed_at ?? item.created_at) ?? "" })}
              {item.resend ? ` · ${t("resent")}` : ""}
            </Link>
            <DeliveryProgress broadcast={item} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * "Send on WhatsApp" on an approved notice (US-2204; FR-MSG-012, FR-MSG-017; docs/17 §5.8):
 * choose sections, see who will get it and who will not (and why), the languages and the
 * estimated Meta cost incl. GST that the school pays Meta, then confirm with the consequence
 * line. The panel then shows live delivery counts. Only `message.send` holders see it, and only
 * where WhatsApp is offered (the settings read answers 404 otherwise).
 */
export function SendNoticePanel({ noticeId }: { noticeId: string }) {
  const t = useTranslations("messaging.send");
  const can = useStaffCan();
  const allowed = can(SEND);
  const settings = useMessagingSettings(allowed);
  const structure = useSchoolStructure();
  const sections = useSectionOptions(structure);
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const legendId = useId();
  const [chosen, setChosen] = useState<Set<string>>(() => new Set());
  const [preview, setPreview] = useState<BroadcastPreview | null>(null);
  const [resend, setResend] = useState(false);
  const [sentId, setSentId] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  // One key per preview: a retried confirmation is answered once (docs/09 §2).
  const confirmKey = useMemo(() => (preview ? newIdempotencyKey() : ""), [preview]);

  if (!allowed || isUnavailable(settings) || settings.status !== "ready") return null;

  function toggle(id: string, on: boolean) {
    setPreview(null);
    setSentId(null);
    setChosen((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function check(again: boolean) {
    setPending(true);
    setError(undefined);
    try {
      const out = await unwrap(
        api.POST("/api/v1/notices/{notice_id}/broadcasts/preview", {
          params: { path: { notice_id: noticeId } },
          body: { section_ids: [...chosen], resend: again },
        }),
      );
      setResend(again);
      setPreview(out);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  async function confirm(reason: string | null) {
    if (!preview) return;
    setPending(true);
    setError(undefined);
    try {
      const out = await unwrap(
        api.POST("/api/v1/notices/{notice_id}/broadcasts", {
          params: { path: { notice_id: noticeId } },
          headers: { "Idempotency-Key": confirmKey },
          body: {
            preview_token: preview.preview_token,
            section_ids: preview.section_ids,
            ...(reason ? { resend_reason: reason } : {}),
          },
        }),
      );
      setSentId(out.id);
      setPreview(null);
      setResend(false);
      await queryClient.invalidateQueries({ queryKey: KEYS.broadcasts });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <div data-print="hide">
      <Card title={t("title")} description={t("description")}>
        <div className="space-y-4">
          <fieldset aria-labelledby={legendId} className="space-y-2">
            <legend id={legendId} className="font-semibold text-ink">
              {t("sections")}
            </legend>
            {sections.length === 0 ? (
              <p className="text-sm text-ink-muted">{t("noSections")}</p>
            ) : (
              <div className="grid gap-1 sm:grid-cols-3">
                {sections.map((section) => (
                  <div key={section.value} className="flex min-h-8 items-center gap-2">
                    <input
                      id={`send-section-${section.value}`}
                      type="checkbox"
                      className="size-5"
                      checked={chosen.has(section.value)}
                      onChange={(event) => toggle(section.value, event.target.checked)}
                    />
                    <label htmlFor={`send-section-${section.value}`}>{section.label}</label>
                  </div>
                ))}
              </div>
            )}
            {sections.length > 1 ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setPreview(null);
                  setSentId(null);
                  setChosen(
                    chosen.size === sections.length
                      ? new Set()
                      : new Set(sections.map((section) => section.value)),
                  );
                }}
              >
                {chosen.size === sections.length ? t("clearAll") : t("chooseAll")}
              </Button>
            ) : null}
          </fieldset>
          <Button
            variant="secondary"
            disabled={chosen.size === 0 || pending}
            aria-disabled={chosen.size === 0 || pending || undefined}
            onClick={() => void check(false)}
          >
            {t("check")}
          </Button>
          {chosen.size === 0 && !preview ? (
            <p className="text-sm text-ink-muted">{t("chooseFirst")}</p>
          ) : null}
          <ApiErrorAlert error={error} namespace="messaging" />
          {preview ? (
            <PreviewCard
              key={preview.preview_token}
              preview={preview}
              resend={resend}
              pending={pending}
              onResend={(on) => void check(on)}
              onConfirm={(reason) => void confirm(reason)}
            />
          ) : null}
          {sentId ? (
            <>
              <Alert tone="success" live>
                {t("queued")}
              </Alert>
              <LiveBroadcast id={sentId} />
            </>
          ) : null}
          <PastBroadcasts noticeId={noticeId} />
        </div>
      </Card>
    </div>
  );
}
