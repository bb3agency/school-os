"use client";

import { useTranslations } from "next-intl";
import { useId, useRef, useState, type FormEvent } from "react";
import { AiPanel, type AiSuggestion } from "@/components/ui/AiPanel";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge, Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, cardClasses } from "@/components/ui/Card";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Icon } from "@/components/ui/Icon";
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

/** Static example questions (i18n), offered as chips that fill the question box. */
const EXAMPLES = ["exams", "circular", "classCount", "certificate"] as const;

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
          ? outcomeOf(state) === "error"
            ? t("failed")
            : t("done")
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

/**
 * The streamed preview (`delta` events): shown as plain text in a dashed, muted box that says
 * it is not checked yet. Its `[n]` markers are not links: citations are validated only when
 * `final` replaces it (docs/06 §5.1). Hidden once the stream stops, ends or is cut off.
 */
function AnswerPreview({ text: preview }: { text: string }) {
  const ta = useTranslations("ask.answer");
  const labelId = useId();
  return (
    <div
      role="group"
      aria-labelledby={labelId}
      className="space-y-2 rounded-lg border border-dashed border-border-control bg-surface-muted p-4"
    >
      <p className="flex flex-wrap items-center gap-2">
        <Pill variant="progress">{ta("draftPill")}</Pill>
        <span id={labelId} className="text-sm font-medium text-ink-muted">
          {ta("previewLabel")}
        </span>
      </p>
      <p className="text-base leading-relaxed whitespace-pre-wrap text-ink-muted">
        {displayText(preview)}
      </p>
    </div>
  );
}

/** Small round mark for the assistant's turn in the conversation (decorative). */
function AssistantMark() {
  return (
    <span
      aria-hidden="true"
      className="ai-gradient flex size-9 shrink-0 items-center justify-center rounded-full text-white"
    >
      <Icon name="sparkles" className="size-4.5" />
    </span>
  );
}

function AnswerView({
  state,
  question,
  canVerify,
  onFollowUp,
}: {
  state: AskState;
  question: string;
  canVerify: boolean;
  onFollowUp: () => void;
}) {
  const t = useTranslations("ask");
  const ta = useTranslations("ask.answer");
  const outcome = outcomeOf(state);
  const busy = state.phase === "waiting" || state.phase === "streaming";

  if (state.phase === "idle") return null;
  if (state.phase === "failed") return <ApiErrorAlert error={state.error} namespace="ask" />;

  // The answer repeats a verified answer when one of its checked sources is one.
  const fromVerified =
    outcome === "answered" &&
    state.citations.some((c) => parseSource(c.source)?.kind === "verified");

  const sources =
    state.citations.length > 0 ? (
      <section aria-labelledby={`${ANCHOR}-sources`} className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id={`${ANCHOR}-sources`} className="text-base font-medium text-ink">
            {outcome === "search_only" ? t("searchOnly.passages") : ta("sources")}
          </h3>
          <Pill variant="tag">{ta("sourcesCount", { count: state.citations.length })}</Pill>
        </div>
        <ol className="grid gap-3 lg:grid-cols-2">
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
    <Card
      title={ta("heading")}
      actions={
        fromVerified ? (
          <Badge tone="success">
            <Icon name="checkCircle" className="size-3.5" />
            {ta("verifiedBadge")}
          </Badge>
        ) : null
      }
    >
      <div className="space-y-5">
        {/* The question, as the right-hand turn of the conversation. */}
        <div className="flex justify-end">
          <div className="max-w-prose rounded-lg rounded-tr-xs bg-surface-sunken px-4 py-3">
            <Eyebrow as="p">{ta("yourQuestion")}</Eyebrow>
            <p className="mt-1 whitespace-pre-line text-ink">{question}</p>
          </div>
        </div>
        <div className="flex items-start gap-3">
          <AssistantMark />
          <div className="min-w-0 flex-1 space-y-4">
            <Eyebrow as="p">{ta("assistant")}</Eyebrow>
            {/* Streamed text is announced once complete (aria-busy while it arrives). */}
            <div aria-live="polite" aria-busy={busy} className="space-y-4">
              {state.phase === "waiting" ? (
                <LoadingState label={t("status.waiting")} rows={2} />
              ) : null}
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
              {outcome === "refused" ? (
                <Alert tone="info" title={t("refused.title")}>
                  {t("refused.body")}
                </Alert>
              ) : null}
              {outcome === "error" ? (
                <Alert tone="danger" title={t("failedAnswer.title")}>
                  {t(`kbErrors.${state.notice ?? "internal"}`)}
                </Alert>
              ) : null}
              {state.phase === "streaming" && !state.finalized && state.preview ? (
                <AnswerPreview text={state.preview} />
              ) : null}
              {(outcome === "answered" || outcome === "pending") && state.text ? (
                <AnswerText text={state.text} citations={state.citations} anchorPrefix={ANCHOR} />
              ) : null}
              {state.finalized &&
              state.replaced &&
              (outcome === "answered" || outcome === "not_found") ? (
                <p className="text-sm text-ink-muted">{ta("replacedNote")}</p>
              ) : null}
              {outcome === "search_only" &&
              state.phase === "done" &&
              state.citations.length === 0 ? (
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
          </div>
        </div>
        {state.phase === "done" && state.queryId && outcome !== "error" ? (
          <div className="space-y-4 border-t border-border pt-4">
            <AnswerFeedback key={state.queryId} queryId={state.queryId} />
            <div className="flex flex-wrap gap-2" data-print="hide">
              <Button variant="secondary" size="sm" onClick={onFollowUp}>
                <Icon name="message" className="size-4" />
                {ta("followUp")}
              </Button>
              {canVerify && outcome === "answered" ? (
                <VerifiedAnswerDialog
                  draft={draftFrom(state, question)}
                  triggerLabel={t("verified.save")}
                />
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/**
 * Ask the school (US-801..803, FR-KB-001..012): one question at a time, the answer streamed
 * through the BFF (an unchecked preview, then the validated answer) with its sources, "not
 * found" when nothing you can see answers it, and plain search-only passages when AI answers
 * are off or the budget is used up (FR-KB-011). The final state comes from `done.status`.
 * Stop aborts the request. The question stays in memory (never in the URL or storage).
 * The example chips only fill the question box; nothing is asked until the member asks.
 */
export function AskScreen() {
  const t = useTranslations("ask");
  const tf = useTranslations("ask.form");
  const tx = useTranslations("ask.examples");
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

  function focusQuestion(select = false) {
    const box = questionRef.current;
    if (!box) return;
    box.focus();
    if (select) box.select();
  }

  const suggestions: AiSuggestion[] = EXAMPLES.map((id) => ({
    id,
    label: tx(id),
    onSelect: () => {
      if (questionRef.current) questionRef.current.value = tx(id);
      setError(undefined);
      focusQuestion();
    },
  }));

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: t("home"), href: "/" }, { label: t("title") }]}
      />
      <AskTabs active="ask" />
      <AiPanel
        eyebrow={t("panel.eyebrow")}
        greeting={t("panel.greeting")}
        description={t("panel.description")}
        suggestions={suggestions}
        suggestionsLabel={tx("label")}
      />
      {/* Outside the blue panel: its white focus ring would vanish on a white card. */}
      <div className={cardClasses({ padding: "md" })}>
        <form noValidate onSubmit={onSubmit} className="space-y-3">
          {" "}
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
          <div className="flex flex-wrap items-center justify-between gap-3">
            <StatusLine state={state} />
            <div className="flex flex-wrap gap-2">
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
              {/* aria-disabled (not disabled) keeps keyboard focus on the button while answering. */}
              <Button type="submit" aria-disabled={busy || undefined}>
                <Icon name="send" className="size-4" />
                {busy ? tf("asking") : tf("submit")}
              </Button>
            </div>
          </div>
        </form>
      </div>
      <AnswerView
        state={state}
        question={question}
        canVerify={can(ASK_PERM.manageVerified)}
        onFollowUp={() => focusQuestion(true)}
      />
    </div>
  );
}
