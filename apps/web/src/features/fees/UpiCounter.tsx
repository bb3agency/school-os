"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Link } from "@/i18n/navigation";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { UTR } from "./data";
import { UpiQr } from "./UpiSettingsScreen";
import { useUpiSettings } from "./upi";

/**
 * Counter help for a UPI payment (FR-FEE-014/015; ADR-0042 decision 9): the school's own static
 * QR to show the parent (collapsed, so the counter fits 1366×768), and a check of the typed UTR:
 * already on another receipt (with a link to it) or already seen in an imported bank statement.
 * The UTR travels in a POST body, never in a URL; the receipt is still issued at once (D19).
 */
export function UpiCounterHelp({ utr }: { utr: string }) {
  const t = useTranslations("fees.counter.upi");
  const api = useBffClient("staff");
  const settings = useUpiSettings();
  const [showQr, setShowQr] = useState(false);
  const value = utr.trim();
  const valid = UTR.test(value);
  const check = useApiQuery(
    ["staff", "fees", "upi", "utr", value],
    () => unwrap(api.POST("/api/v1/fees/upi/utr-check", { body: { utr: value } })),
    { enabled: valid },
  );
  const configured = settings.status === "ready" && settings.data.configured;
  return (
    <div className="space-y-2 md:col-span-2">
      {configured ? (
        <div>
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={showQr}
            onClick={() => setShowQr((v) => !v)}
          >
            {showQr ? t("hideQr") : t("showQr")}
          </Button>
          {showQr && settings.status === "ready" ? (
            <div className="mt-2">
              <UpiQr settings={settings.data} size={160} />
            </div>
          ) : null}
        </div>
      ) : settings.status === "ready" ? (
        <p className="text-sm text-ink-muted">{t("noQr")}</p>
      ) : null}
      {valid && check.status === "ready" && check.data.state === "in_use" ? (
        <Alert tone="warning" live>
          {t("inUse")}{" "}
          {check.data.receipt_id ? (
            <Link
              href={`/fees/receipts/${check.data.receipt_id}`}
              className="font-semibold underline underline-offset-4"
            >
              {check.data.receipt_number}
            </Link>
          ) : null}
        </Alert>
      ) : null}
      {valid && check.status === "ready" && check.data.seen_in_bank ? (
        <p className="text-sm text-ink-muted" role="status">
          {t("seenInBank")}
        </p>
      ) : null}
    </div>
  );
}
