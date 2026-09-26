"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { useLocale } from "next-intl";
import { createBffClient } from "@/lib/bff/fetch";
import { defaultNavigate, type Navigate } from "@/lib/bff/session-client";

/**
 * "Check again" on the no-access page: accept any invitation the office has re-sent since
 * sign-in (POST /me/accept-invitations through the BFF), then go to the school picker.
 */
export function CheckInvitationsButton({ navigate = defaultNavigate }: { navigate?: Navigate }) {
  const t = useTranslations("noAccess");
  const locale = useLocale();
  const [state, setState] = useState<"idle" | "busy" | "none" | "failed">("idle");

  async function check() {
    setState("busy");
    try {
      const api = createBffClient({ kind: "staff", locale, navigate });
      const { data, response } = await api.POST("/api/v1/me/accept-invitations");
      if (!response.ok) {
        setState("failed");
        return;
      }
      const schools = await api.GET("/api/v1/me/schools");
      if ((schools.data?.data.length ?? 0) > 0 || (data?.accepted.length ?? 0) > 0) {
        navigate(`/${locale}/choose-school`);
        return;
      }
      setState("none");
    } catch {
      setState("failed");
    }
  }

  return (
    <div className="space-y-3">
      <Button onClick={check} disabled={state === "busy"}>
        {state === "busy" ? t("checking") : t("checkAgain")}
      </Button>
      {state === "none" ? (
        <Alert tone="info" live>
          {t("stillNone")}
        </Alert>
      ) : null}
      {state === "failed" ? (
        <Alert tone="danger" live>
          {t("checkFailed")}
        </Alert>
      ) : null}
    </div>
  );
}
