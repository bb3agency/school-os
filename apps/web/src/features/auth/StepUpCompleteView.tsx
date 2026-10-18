"use client";

import { useTranslations } from "next-intl";
import { useEffect } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { STEP_UP_CHANNEL, STEP_UP_COMPLETE } from "@/lib/bff/step-up";

/**
 * Last page of the step-up window (ADR-0018): tells the SchoolOS page that opened it that the
 * user signed in again (BroadcastChannel, same origin only, no data), then tries to close
 * itself. If the browser keeps it open, it says so and offers a close button.
 */
export function StepUpCompleteView() {
  const t = useTranslations("errors.stepUp");

  useEffect(() => {
    if (typeof BroadcastChannel !== "undefined") {
      const channel = new BroadcastChannel(STEP_UP_CHANNEL);
      channel.postMessage(STEP_UP_COMPLETE);
      channel.close();
    }
    // Allowed for windows opened by a script; ignored otherwise.
    window.close();
  }, []);

  return (
    <div className="mx-auto max-w-xl space-y-4">
      <span className="flex size-12 items-center justify-center rounded-full bg-success-soft text-success-ink">
        <Icon name="check" className="size-6" />
      </span>
      <h1 className="text-2xl font-semibold text-ink">{t("completeTitle")}</h1>
      <Alert tone="success">{t("completeBody")}</Alert>
      <Button variant="secondary" onClick={() => window.close()}>
        {t("closeWindow")}
      </Button>
    </div>
  );
}
