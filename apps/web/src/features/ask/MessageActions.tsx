"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Icon, type IconName } from "@/components/ui/Icon";
import { useApiMutation } from "@/lib/bff/query";
import { cn } from "@/lib/cn";
import type { AskCitation } from "./answer";
import { FEEDBACK_REASONS, useKnowledgeApi, type FeedbackBody, type FeedbackReason } from "./data";
import { toPlainText } from "./markdown";

/** Small round icon button of the message action rows (32px; label in a tooltip on hover). */
export function ActionButton({
  icon,
  label,
  onClick,
  pressed,
  expanded,
  disabled,
  children,
}: {
  icon: IconName;
  label: string;
  onClick: () => void;
  pressed?: boolean;
  expanded?: boolean;
  disabled?: boolean;
  children?: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      aria-expanded={expanded}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "inline-flex size-8 shrink-0 items-center justify-center rounded-md text-ink-muted",
        "hover:bg-surface-muted hover:text-ink disabled:cursor-not-allowed disabled:opacity-60",
        "motion-safe:hover:transition-colors",
        pressed && "bg-primary-soft text-primary hover:bg-primary-soft hover:text-primary",
      )}
    >
      <Icon name={icon} className="size-4" />
      {children}
    </button>
  );
}

/**
 * The text copied for an answer: plain prose with its `[n]` markers and a numbered source
 * list ("Sources: [1] Exam circular, page 1"), so a pasted answer still says where it comes from.
 */
export function copyText(
  text: string,
  citations: readonly AskCitation[],
  sourcesHeading: string,
  untitled: string,
  pageLabel: (page: number) => string,
): string {
  const known = new Set(citations.map((c) => c.index));
  const body = toPlainText(text, known, true);
  if (citations.length === 0) return body;
  const lines = citations.map((c) => {
    const page = /#p(\d+)$/.exec(c.source)?.[1];
    const title = c.title.trim() || untitled;
    return `[${c.index}] ${title}${page ? `, ${pageLabel(Number(page))}` : ""}`;
  });
  return `${body}\n\n${sourcesHeading}:\n${lines.join("\n")}`;
}

async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (!navigator.clipboard?.writeText) return false;
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Copy with a short "copied" check (announced politely). */
export function CopyButton({
  label,
  doneLabel,
  text,
}: {
  label: string;
  doneLabel: string;
  text: () => string;
}) {
  const t = useTranslations("ask.actions");
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  return (
    <>
      <ActionButton
        icon={state === "copied" ? "check" : "copy"}
        label={state === "copied" ? doneLabel : label}
        onClick={() => {
          void writeClipboard(text()).then((ok) => {
            setState(ok ? "copied" : "failed");
            if (timer.current) clearTimeout(timer.current);
            timer.current = setTimeout(() => setState("idle"), ok ? 2000 : 6000);
          });
        }}
      />
      <span role="status" className={state === "failed" ? "text-xs text-danger" : "sr-only"}>
        {state === "copied" ? doneLabel : state === "failed" ? t("copyFailed") : ""}
      </span>
    </>
  );
}

/**
 * Thumbs up / down (US-801 AC4, FR-KB-009): helpful is sent at once; not helpful opens the
 * reason codes (never free text) first. Only your own answers (404 for anyone else's).
 */
export function FeedbackButtons({
  queryId,
  initial,
}: {
  queryId: string;
  initial: "helpful" | "not_helpful" | null;
}) {
  const t = useTranslations("ask.feedback");
  const api = useKnowledgeApi();
  const legendId = useId();
  const [choice, setChoice] = useState<"helpful" | "not_helpful" | null>(initial);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState<FeedbackReason | "">("");
  const send = useApiMutation((body: FeedbackBody) => api.feedback(queryId, body));

  const sent = send.isSuccess;
  return (
    <>
      <ActionButton
        icon="thumbsUp"
        label={t("helpful")}
        pressed={choice === "helpful"}
        disabled={send.isPending}
        onClick={() => {
          setChoice("helpful");
          setAsking(false);
          send.mutate({ feedback: "helpful" });
        }}
      />
      <ActionButton
        icon="thumbsDown"
        label={t("notHelpful")}
        pressed={choice === "not_helpful"}
        expanded={asking}
        disabled={send.isPending}
        onClick={() => {
          setChoice("not_helpful");
          setAsking(true);
        }}
      />
      {sent ? (
        <span role="status" className="chat-fade text-xs text-ink-muted">
          {t("thanks")}
        </span>
      ) : null}
      {asking && !sent ? (
        <form
          className="chat-enter basis-full space-y-3 rounded-lg border border-border bg-surface-muted p-3"
          onSubmit={(event) => {
            event.preventDefault();
            send.mutate({ feedback: "not_helpful", ...(reason ? { reason } : {}) });
          }}
        >
          <fieldset className="space-y-2">
            <legend id={legendId} className="text-sm font-semibold">
              {t("reasonLegend")}
            </legend>
            {FEEDBACK_REASONS.map((code) => (
              <label key={code} className="flex min-h-6 items-center gap-2 text-sm">
                <input
                  type="radio"
                  name={`reason-${queryId}`}
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
      {send.error ? (
        <div className="basis-full">
          <ApiErrorAlert error={send.error} namespace="ask" />
        </div>
      ) : null}
    </>
  );
}

/** "‹ 2 / 2 ›" between the answers of a regenerated or edited question. */
export function VersionSwitcher({
  index,
  total,
  onChange,
}: {
  index: number;
  total: number;
  onChange: (index: number) => void;
}) {
  const t = useTranslations("ask.actions");
  if (total < 2) return null;
  return (
    <span
      className="inline-flex items-center text-xs text-ink-muted"
      role="group"
      aria-label={t("version", { index: index + 1, total })}
    >
      <ActionButton
        icon="chevronLeft"
        label={t("previousVersion")}
        disabled={index === 0}
        onClick={() => onChange(index - 1)}
      />
      <span aria-hidden="true" className="min-w-8 text-center tabular-nums">
        {index + 1} / {total}
      </span>
      <ActionButton
        icon="chevronRight"
        label={t("nextVersion")}
        disabled={index === total - 1}
        onClick={() => onChange(index + 1)}
      />
    </span>
  );
}
