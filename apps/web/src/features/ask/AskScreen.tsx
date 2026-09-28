"use client";

import { useTranslations } from "next-intl";
import { useRef, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { containsAadhaarNumber } from "@/lib/aadhaar";
import { text } from "@/lib/validation";
import { displayText, outcomeOf, parseSource, quoteFromSnippet, type AskState } from "./answer";
import { ASK_PERM } from "./data";
import { AnswerFeedback } from "./Feedback";
import { AnswerText, AskTabs, SourceChip, sourceAnchor } from "./parts";
import { useAsk } from "./useAsk";
import { VerifiedAnswerDialog, type VerifiedDraft } from "./VerifiedAnswerDialog";

const MAX_QUESTION = 1000;
const questionSchema = text(MAX_QUESTION);
const ANCHOR = "ask";

/** Prefill for "Save as verified answer": document sources only (the API accepts no others). */
function draftFrom(state: AskState, question: string): VerifiedDraft {
  const seen = new Set<string>();
  const citations = state.citations
    .filter((c) => parseSource(c.source)?.kind === "doc" && !seen.has(c.source))
    .map((c) => {
      seen.add(c.source);
      return { source: c.source, cited_text: quoteFromSnippet(c.snippet) };
    });
  return {
    question: question.slice(0, 500),
    language: state.language ?? "en",
    answer_text: displayText(state.text)
      .replace(/\s*\[\d{1,3}\]/g, "")
      .trim()
      .slice(0, 5000),
    citations,
  };
}

function StatusLine({ state }: { state: AskState }) {
  const t = useTranslations("ask.status");
  const message =
    state.phase === "waiting"
      ? t("waiting")
      : state.phase === "streaming"
        ? t("streaming")
        : state.phase === "done"
          ? t("done")
          : state.phase === "stopped"
            ? t("stopped")
            : state.phase === "interrupted"
              ? t("interrupted")
              : "";
  return (
    <p role="status" aria-live="polite" className="min-h-5 text-sm text-ink-muted">
      {message}
    </p>
  );
}

function AnswerView({
  state,
  question,
  canVerify,
}: {
  state: AskState;
  question: string;
  canVerify: boolean;
}) {
  const t = useTranslations("ask");
  const ta = useTranslations("ask.answer");
  const outcome = outcomeOf(state);
  const busy = state.phase === "waiting" || state.phase === "streaming";

  if (state.phase === "idle") return null;
  if (state.phase === "failed") return <ApiErrorAlert error={state.error} namespace="ask" />;

  const sources =
    state.citations.length > 0 ? (
      <section aria-labelledby={`${ANCHOR}-sources`} className="space-y-2">
        <h3 id={`${ANCHOR}-sources`} className="text-base font-semibold">
          {outcome === "search_only" ? t("searchOnly.passages") : ta("sources")}
        </h3>
        <ol className="space-y-2">
          {state.citations.map((c) => (
            <SourceChip
              key={c.index}
              index={c.index}
              source={c.source}
              title={c.title}
              quote={c.snippet}
              anchorId={sourceAnchor(ANCHOR, c.index)}
            />
          ))}
        </ol>
      </section>
    ) : null;

  return (
    <Card title={ta("heading")}>
      <div className="space-y-4">
        <div>
          <p className="text-sm font-semibold text-ink-muted">{ta("yourQuestion")}</p>
          <p className="whitespace-pre-line">{question}</p>
        </div>
        {/* Streamed text is announced once complete (aria-busy while it arrives). */}
        <div aria-live="polite" aria-busy={busy} className="space-y-4">
          {outcome === "search_only" ? (
            <Alert tone="warning" title={t("searchOnly.title")}>
              <p>{t(`kbErrors.${state.notice ?? "unavailable"}`)}</p>
              <p className="mt-1">{t("searchOnly.body")}</p>
            </Alert>
          ) : null}
          {outcome === "not_found" ? (
            <Alert tone="info" title={t("notFound.title")}>
              {t("notFound.body")}
            </Alert>
          ) : null}
          {outcome !== "search_only" && outcome !== "not_found" && state.text ? (
            <AnswerText text={state.text} citations={state.citations} anchorPrefix={ANCHOR} />
          ) : null}
          {outcome === "search_only" && state.phase === "done" && state.citations.length === 0 ? (
            <p>{t("searchOnly.none")}</p>
          ) : null}
          {sources}
          {state.phase === "stopped" ? <Alert tone="info">{ta("stoppedNote")}</Alert> : null}
          {state.phase === "interrupted" ? (
            <Alert tone="warning" title={ta("interruptedTitle")}>
              {ta("interruptedBody")}
            </Alert>
          ) : null}
        </div>
        {state.phase === "done" && state.queryId ? (
          <div className="space-y-3 border-t border-border pt-4">
            <AnswerFeedback key={state.queryId} queryId={state.queryId} />
            {canVerify && outcome === "answered" ? (
              <VerifiedAnswerDialog
                draft={draftFrom(state, question)}
                triggerLabel={t("verified.save")}
              />
            ) : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/**
 * Ask the school (US-801..803, FR-KB-001..012): one question at a time, the answer streamed
 * through the BFF with its sources, "not found" when nothing you can see answers it, and
 * plain search-only passages when AI answers are off or the budget is used up (FR-KB-011).
 * Stop aborts the request. The question stays in memory (never in the URL or storage).
 */
export function AskScreen() {
  const t = useTranslations("ask");
  const tf = useTranslations("ask.form");
  const tv = useTranslations("validation");
  const tc = useTranslations("common");
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const { state, question, ask, stop, busy } = useAsk();
  const [error, setError] = useState<string | undefined>(undefined);
  const questionRef = useRef<HTMLTextAreaElement>(null);

  if (me.isPending) return <LoadingState label={tc("loading")} />;
  if (!can(ASK_PERM.ask)) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} />
        <AskTabs active="ask" />
        <Alert tone="warning" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      </div>
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const field = event.currentTarget.elements.namedItem("question") as HTMLTextAreaElement;
    const parsed = questionSchema.safeParse(field.value);
    if (!parsed.success) {
      setError(tv(parsed.error.issues[0]?.message === "tooLong" ? "tooLong" : "required"));
      field.focus();
      return;
    }
    // Invariant 4: a full Aadhaar number never leaves the browser (the API masks it too).
    if (containsAadhaarNumber(parsed.data)) {
      setError(tv("noAadhaar"));
      field.focus();
      return;
    }
    setError(undefined);
    ask(parsed.data);
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <AskTabs active="ask" />
      <Card>
        <form noValidate onSubmit={onSubmit} className="space-y-3">
          <TextAreaField
            name="question"
            label={tf("question")}
            hint={`${tf("hint")} ${tf("shortcut")}`}
            maxLength={MAX_QUESTION}
            rows={3}
            error={error}
            ref={questionRef}
            onKeyDown={(event) => {
              // Ctrl+Enter asks, as in chat boxes; Enter alone adds a line.
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
          />
          <div className="flex flex-wrap gap-2">
            {/* aria-disabled (not disabled) keeps keyboard focus on the button while answering. */}
            <Button type="submit" aria-disabled={busy || undefined}>
              {busy ? tf("asking") : tf("submit")}
            </Button>
            {busy ? (
              <Button
                variant="secondary"
                onClick={() => {
                  stop();
                  questionRef.current?.focus();
                }}
              >
                {tf("stop")}
              </Button>
            ) : null}
          </div>
          <StatusLine state={state} />
        </form>
      </Card>
      <AnswerView state={state} question={question} canVerify={can(ASK_PERM.manageVerified)} />
    </div>
  );
}
