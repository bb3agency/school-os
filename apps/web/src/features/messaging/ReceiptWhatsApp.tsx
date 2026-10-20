"use client";

import { useTranslations } from "next-intl";
import { useMemo } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import {
  newIdempotencyKey,
  unwrap,
  useApiMutation,
  useApiQuery,
  useBffClient,
} from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { translateOr } from "@/lib/i18n-dynamic";
import { KEYS, SEND, type ReceiptWhatsApp as State } from "./data";

/** Who may send a receipt on WhatsApp: the counter (`fee.collect`) or a sender. */
const SENDERS = ["fee.collect", SEND] as const;

function StatusLine({ state }: { state: State }) {
  const t = useTranslations("messaging.receipt");
  const tr = useTranslations("messaging.log.reasons");
  const last = state.messages.at(-1);
  if (last) {
    const reason = translateOr(tr, last.failure_reason ?? last.skip_reason ?? "other", "other");
    switch (last.status) {
      case "queued":
        return <>{t("queued")}</>;
      case "sent":
        return <>{t("sent")}</>;
      case "delivered":
        return <>{t("delivered")}</>;
      case "read":
        return <>{t("read")}</>;
      case "failed":
        return <>{t("failed", { reason })}</>;
      default:
        return <>{t("skipped", { reason })}</>;
    }
  }
  if (state.blocked_reason === "no_opted_in_guardian") return <>{t("noOptin")}</>;
  if (state.blocked_reason === "receipt_cancelled") return <>{t("cancelled")}</>;
  if (state.blocked_reason) return <>{t("blocked")}</>;
  return <>{t("hint", { count: state.opted_in_guardians })}</>;
}

/**
 * "Send on WhatsApp" for one receipt (FR-MSG-026, US-2206; docs/17 §5.8): a secondary button
 * and one status line, on the counter's saved panel and the receipt page. It never takes focus
 * (Print keeps it on the counter) and has no shortcut. Hidden where WhatsApp is not offered or
 * not connected, and for people who neither collect fees nor send messages. A receipt goes to a
 * guardian once, whoever asks (the automatic receipt message included).
 */
export function ReceiptWhatsApp({ receiptId }: { receiptId: string }) {
  const t = useTranslations("messaging.receipt");
  const tc = useTranslations("common");
  const can = useStaffCan();
  const allowed = can(SENDERS);
  const api = useBffClient("staff");
  const state = useApiQuery(
    KEYS.receipt(receiptId),
    () =>
      unwrap(
        api.GET("/api/v1/fees/receipts/{receipt_id}/whatsapp", {
          params: { path: { receipt_id: receiptId } },
        }),
      ),
    { enabled: allowed },
  );
  // One key per receipt shown: a retried click is answered once (docs/09 §2).
  const key = useMemo(() => newIdempotencyKey(), []);
  const send = useApiMutation(
    () =>
      unwrap(
        api.POST("/api/v1/fees/receipts/{receipt_id}/whatsapp", {
          params: { path: { receipt_id: receiptId } },
          headers: { "Idempotency-Key": key },
        }),
      ),
    { invalidate: [KEYS.receipt(receiptId)] },
  );
  if (!allowed || state.status !== "ready") return null;
  const data = send.data ?? state.data;
  if (data.blocked_reason === "messaging_not_connected") return null;
  return (
    <div className="space-y-2" data-print="hide">
      <div className="flex flex-wrap items-center gap-3">
        {data.can_send ? (
          <Button
            variant="secondary"
            disabled={send.isPending}
            aria-disabled={send.isPending || undefined}
            onClick={() => send.mutate(undefined)}
          >
            {send.isPending ? tc("working") : t("send")}
          </Button>
        ) : null}
        <p role="status" className="text-sm text-ink-muted">
          <span className="font-semibold text-ink">{t("label")}: </span>
          <StatusLine state={data} />
        </p>
      </div>
      <ApiErrorAlert error={send.error ?? undefined} namespace="messaging" />
    </div>
  );
}
