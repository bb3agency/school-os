"use client";

import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { useApiMutation } from "@/lib/bff/query";
import { FEEDBACK_REASONS, useKnowledgeApi, type FeedbackReason } from "./data";

/**
 * "Was this answer helpful?" (US-801 AC4, FR-KB-009): helpful is sent at once; not helpful
 * asks for an optional reason CODE (never free text) first. Only your own answers (the API
 * answers 404 for anyone else's).
 */
export function AnswerFeedback({ queryId }: { queryId: string }) {
  const t = useTranslations("ask.feedback");
  const api = useKnowledgeApi();
  const legendId = useId();
  const [choice, setChoice] = useState<"helpful" | "not_helpful" | null>(null);
  const [reason, setReason] = useState<FeedbackReason | "">("");
  const send = useApiMutation(
    (body: { feedback: "helpful" | "not_helpful"; reason?: FeedbackReason }) =>
      api.feedback(queryId, body),
  );

  if (send.isSuccess) {
    return (
      <Alert tone="success" live>
        {t("thanks")}
      </Alert>
    );
  }

  return (
    <div className="space-y-3" data-print="hide">
      <p id={legendId} className="text-sm font-semibold">
        {t("question")}
      </p>
      <div role="group" aria-labelledby={legendId} className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size="sm"
          aria-pressed={choice === "helpful"}
          disabled={send.isPending}
          onClick={() => {
            setChoice("helpful");
            send.mutate({ feedback: "helpful" });
          }}
        >
          {t("helpful")}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          aria-pressed={choice === "not_helpful"}
          aria-expanded={choice === "not_helpful"}
          disabled={send.isPending}
          onClick={() => setChoice("not_helpful")}
        >
          {t("notHelpful")}
        </Button>
      </div>
      {choice === "not_helpful" ? (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            send.mutate({ feedback: "not_helpful", ...(reason ? { reason } : {}) });
          }}
        >
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold">{t("reasonLegend")}</legend>
            {FEEDBACK_REASONS.map((code) => (
              <label key={code} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="reason"
                  value={code}
                  checked={reason === code}
                  onChange={() => setReason(code)}
                  className="size-4"
                />
                {t(`reasons.${code}`)}
              </label>
            ))}
          </fieldset>
          <Button type="submit" size="sm" disabled={send.isPending}>
            {send.isPending ? t("sending") : t("send")}
          </Button>
        </form>
      ) : null}
      <ApiErrorAlert error={send.error ?? undefined} namespace="ask" />
    </div>
  );
}
