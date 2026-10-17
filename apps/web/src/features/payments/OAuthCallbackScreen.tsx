"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Link } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { KEYS } from "./data";

type State =
  { kind: "working" } | { kind: "done" } | { kind: "missing" } | { kind: "failed"; error: unknown };

/**
 * Razorpay's OAuth redirect lands here (FR-PAY-002; `SOS_RAZORPAY_OAUTH_REDIRECT_URI`). The
 * `code` and `state` are read once from the address, removed from it at once (history and the
 * page's own links never keep them), and posted to the API in a body. The API checks that the
 * single-use state belongs to this member and school before exchanging the code.
 */
export function OAuthCallbackScreen() {
  const t = useTranslations("payments.callback");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [state, setState] = useState<State>({ kind: "working" });
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const oauthState = params.get("state");
    window.history.replaceState(null, "", window.location.pathname);
    const finish: Promise<State> =
      !code || !oauthState
        ? Promise.resolve({ kind: "missing" })
        : unwrap(
            api.POST("/api/v1/payments/connection/oauth/callback", {
              body: { code, state: oauthState },
            }),
          ).then(async (): Promise<State> => {
            await queryClient.invalidateQueries({ queryKey: KEYS.all });
            return { kind: "done" };
          });
    finish.then(setState).catch((error: unknown) => setState({ kind: "failed", error }));
  }, [api, queryClient]);

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} />
      {state.kind === "working" ? <LoadingState label={t("working")} /> : null}
      {state.kind === "done" ? (
        <Alert tone="success" title={t("doneTitle")}>
          {t("doneBody")}
        </Alert>
      ) : null}
      {state.kind === "missing" ? (
        <Alert tone="warning" title={t("missingTitle")}>
          {t("missingBody")}
        </Alert>
      ) : null}
      {state.kind === "failed" ? <ApiErrorAlert error={state.error} namespace="payments" /> : null}
      {state.kind !== "working" ? (
        <p>
          <Link href="/settings/payments" className="text-primary underline">
            {t("back")}
          </Link>
        </p>
      ) : null}
    </div>
  );
}
