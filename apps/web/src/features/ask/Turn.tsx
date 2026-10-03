"use client";

import { useTranslations } from "next-intl";
import { lazy, memo, Suspense, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge, Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { containsAadhaarNumber } from "@/lib/aadhaar";
import { cn } from "@/lib/cn";
import {
  messageAnchor,
  outcomeOf,
  parseSource,
  quoteFromSnippet,
  type AskCitation,
  type AskState,
} from "./answer";
import { CitationChip, SourceCard, sourceCardId } from "./Citations";
import { MAX_QUESTION, shouldSend } from "./Composer";
import { askError } from "./errors";
import { stableStreamingText } from "./markdown-parse";
import { MemoryNotes } from "./MemoryNotes";
import {
  ActionButton,
  copyText,
  CopyButton,
  FeedbackButtons,
  VersionSwitcher,
} from "./MessageActions";
import { useSmoothText } from "./motion";
import { LiveStatus, WorkedFor } from "./Status";
import { VerifiedAnswerDialog, type VerifiedDraft } from "./VerifiedAnswerDialog";

// The markdown renderer loads after the chat shell (keeps the route's first JS lean); until
// then the text shows as plain paragraphs.
const Markdown = lazy(() => import("./Markdown"));

const NO_CITATIONS: ReadonlySet<number> = new Set();

function PlainText({ text, trailing }: { text: string; trailing?: ReactNode }) {
  return (
    <p className="text-base leading-relaxed whitespace-pre-line text-ink">
      {text}
      {trailing}
    </p>
  );
}

/** Prefill for "Save as verified answer": document sources only (the API accepts no others). */
export function draftFrom(state: AskState, question: string): VerifiedDraft {
  const seen = new Set<string>();
  const citations = state.citations
    .filter((c) => parseSource(c.source)?.kind === "doc" && !seen.has(c.source))
    .map((c) => {
      seen.add(c.source);
      return { source: c.source, cited_text: quoteFromSnippet(c.snippet) };
    });
  const known = new Set(state.citations.map((c) => c.index));
  return {
    question: question.slice(0, 500),
    language: state.language ?? "en",
    answer_text: state.text
      .replace(/\s*\[(\d{1,3})\]/g, (all, n: string) => (known.has(Number(n)) ? "" : all))
      .replace(/<\/?[A-Za-z][^<>]*>/g, "")
      .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/(\*\*|__)/g, "")
      .trim()
      .slice(0, 5000),
    citations,
  };
}

/** Small round mark for the assistant's turn (decorative). */
function AssistantMark() {
  return (
    <span
      aria-hidden="true"
      className="ai-gradient mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full text-white"
    >
      <Icon name="sparkles" className="size-4" />
    </span>
  );
}

export interface TurnModel {
  /** Stable key: the query id, or the live turn's local key. */
  key: string;
  question: string;
  state: AskState;
  live: boolean;
  /** The last turn of the thread (actions always visible, follow-ups, edit). */
  latest: boolean;
  feedback: "helpful" | "not_helpful" | null;
  versions: { index: number; total: number; group: string } | null;
  /** An older turn: rendered lazily where the browser supports it. */
  past: boolean;
}

export interface TurnHandlers {
  busy: boolean;
  canVerify: boolean;
  onVersion: (group: string, index: number) => void;
  onRegenerate: (queryId: string, question: string) => void;
  onEdit: (queryId: string, question: string) => string | null;
  onRetry: (turn: TurnModel) => void;
  onFollowUp: (question: string) => void;
}

/** The question, right-aligned; the latest one can be edited in place (Cancel / Send). */
function UserMessage({ turn, handlers }: { turn: TurnModel; handlers: TurnHandlers }) {
  const t = useTranslations("ask");
  const ta = useTranslations("ask.actions");
  const tv = useTranslations("validation");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(turn.question);
  const [error, setError] = useState<string | null>(null);
  const composing = useRef(false);
  const errorId = useId();
  const labelId = useId();
  const queryId = turn.state.queryId;
  const canEdit = turn.latest && !handlers.busy && queryId !== null && !turn.live;

  const submit = () => {
    if (!queryId) return;
    const trimmed = draft.trim();
    if (!trimmed) return setError(tv("required"));
    if (containsAadhaarNumber(trimmed)) return setError(tv("noAadhaar"));
    const failure = handlers.onEdit(queryId, trimmed);
    if (failure) return setError(failure);
    setEditing(false);
  };

  if (editing) {
    return (
      <form
        className="chat-enter ms-auto w-full max-w-2xl space-y-2"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <label id={labelId} htmlFor={`${labelId}-box`} className="sr-only">
          {ta("editLabel")}
        </label>
        <textarea
          id={`${labelId}-box`}
          value={draft}
          maxLength={MAX_QUESTION}
          rows={3}
          // The edit box opens on request, so it takes focus (like the dialogs do).
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(event) => {
            setDraft(event.target.value);
            setError(null);
          }}
          onCompositionStart={() => {
            composing.current = true;
          }}
          onCompositionEnd={() => {
            composing.current = false;
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              setEditing(false);
            } else if (shouldSend(event, composing.current, false)) {
              event.preventDefault();
              submit();
            }
          }}
          className="block w-full resize-y rounded-2xl border border-border-control bg-surface px-4 py-3 text-base leading-relaxed"
        />
        {error ? (
          <p id={errorId} role="alert" className="text-sm font-semibold text-danger">
            {error}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={() => setEditing(false)}>
            {ta("editCancel")}
          </Button>
          <Button type="submit" size="sm">
            {ta("editSend")}
          </Button>
        </div>
      </form>
    );
  }

  return (
    <div className="group/q chat-enter flex flex-col items-end gap-1">
      <div className="max-w-[min(85%,42rem)] rounded-2xl rounded-ee-md bg-surface-sunken px-4 py-2.5 text-base leading-relaxed whitespace-pre-wrap text-ink break-anywhere">
        <span className="sr-only">{`${t("chat.you")}: `}</span>
        {turn.question}
      </div>
      <div
        className={cn(
          "chat-actions flex items-center gap-0.5",
          "opacity-0 group-hover/q:opacity-100 group-focus-within/q:opacity-100 [@media(hover:none)]:opacity-100",
        )}
      >
        <CopyButton label={t("chat.you")} doneLabel={ta("copied")} text={() => turn.question} />
        {canEdit ? (
          <ActionButton
            icon="pencil"
            label={ta("edit")}
            onClick={() => {
              setDraft(turn.question);
              setError(null);
              setEditing(true);
            }}
          />
        ) : null}
      </div>
    </div>
  );
}

function Sources({ state, prefix }: { state: AskState; prefix: string }) {
  const t = useTranslations("ask");
  const headingId = useId();
  if (state.citations.length === 0) return null;
  const searchOnly = outcomeOf(state) === "search_only";
  return (
    <section aria-labelledby={headingId} className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id={headingId} className="text-sm font-semibold text-ink">
          {searchOnly ? t("searchOnly.passages") : t("answer.sources")}
        </h3>
        <Pill variant="tag">{t("answer.sourcesCount", { count: state.citations.length })}</Pill>
      </div>
      <ol className="grid gap-2 sm:grid-cols-2">
        {state.citations.map((c) => (
          <SourceCard key={c.index} citation={c} id={sourceCardId(prefix, c.index)} />
        ))}
      </ol>
    </section>
  );
}

/** The streamed, unchecked preview: revealed smoothly with a caret, marked as a draft. */
function Preview({ text, streaming }: { text: string; streaming: boolean }) {
  const t = useTranslations("ask.answer");
  const labelId = useId();
  const stable = streaming ? stableStreamingText(text) : text;
  const shown = useSmoothText(stable, streaming);
  const caret = streaming ? <span aria-hidden="true" className="chat-caret" /> : undefined;
  return (
    <div role="group" aria-labelledby={labelId} className="space-y-1.5">
      <p id={labelId} className="flex items-center gap-1.5 text-xs text-ink-subtle">
        <Icon name="clock" className="size-3.5" />
        {t("previewLabel")}
      </p>
      <div className={cn(!streaming && "text-ink-muted")}>
        <Suspense fallback={<PlainText text={shown} trailing={caret} />}>
          <Markdown
            text={shown}
            citations={NO_CITATIONS}
            cite={() => null}
            trailing={caret}
            className={cn(!streaming && "text-ink-muted")}
          />
        </Suspense>
      </div>
    </div>
  );
}

/** The checked answer with its citation chips. */
function FinalText({ state, prefix, fade }: { state: AskState; prefix: string; fade: boolean }) {
  const byIndex = useMemo(
    () => new Map<number, AskCitation>(state.citations.map((c) => [c.index, c])),
    [state.citations],
  );
  const known = useMemo(() => new Set(byIndex.keys()), [byIndex]);
  const cite = (index: number) => {
    const citation = byIndex.get(index);
    return citation ? <CitationChip citation={citation} prefix={prefix} /> : `[${index}]`;
  };
  return (
    <div className={cn(fade && "chat-fade")}>
      <Suspense fallback={<PlainText text={state.text} />}>
        <Markdown text={state.text} citations={known} cite={cite} />
      </Suspense>
    </div>
  );
}

/** The assistant's side of one turn: status, answer, sources, notes and actions. */
function AssistantMessage({ turn, handlers }: { turn: TurnModel; handlers: TurnHandlers }) {
  const t = useTranslations("ask");
  const ta = useTranslations("ask.actions");
  const tanswer = useTranslations("ask.answer");
  const { state } = turn;
  const outcome = outcomeOf(state);
  const busy = state.phase === "waiting" || state.phase === "streaming";
  const prefix = `ask-${state.queryId ?? turn.key}`;
  const queryId = state.queryId;
  const fromVerified =
    outcome === "answered" &&
    !state.withheld &&
    state.citations.some((c) => parseSource(c.source)?.kind === "verified");
  const finished = state.phase === "done" || state.phase === "stopped";
  const showText = (outcome === "answered" || outcome === "pending") && state.text !== "";
  const stoppedPreview = state.phase === "stopped" && !state.finalized && state.preview !== "";

  return (
    <article
      aria-label={tanswer("heading")}
      aria-busy={busy}
      className={cn("chat-enter flex gap-3 rounded-xl")}
    >
      <AssistantMark />
      <div className="min-w-0 flex-1 space-y-3">
        {busy && !state.finalized ? <LiveStatus state={state} /> : null}
        {finished && outcome !== "error" ? <WorkedFor state={state} /> : null}
        {fromVerified ? (
          <Badge tone="success">
            <Icon name="checkCircle" className="size-3.5" />
            {tanswer("verifiedBadge")}
          </Badge>
        ) : null}
        {state.cached && finished ? (
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-subtle">
            <Icon name="refresh" className="size-4" />
            <span>{ta("cached")}</span>
            {queryId && turn.latest && !handlers.busy ? (
              <button
                type="button"
                onClick={() => handlers.onRegenerate(queryId, turn.question)}
                className="min-h-6 font-semibold text-primary underline underline-offset-4 hover:no-underline"
              >
                {ta("fresh")}
              </button>
            ) : null}
          </p>
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
        {outcome === "error" && state.phase === "done" ? (
          <Alert tone="danger" title={t("failedAnswer.title")}>
            {t(`kbErrors.${state.notice ?? "internal"}`)}
          </Alert>
        ) : null}
        {busy && !state.finalized && state.preview ? (
          <Preview text={state.preview} streaming />
        ) : null}
        {stoppedPreview ? (
          <div className="space-y-2">
            <Pill variant="dark">{ta("stopped")}</Pill>
            <Preview text={state.preview} streaming={false} />
          </div>
        ) : null}
        {showText ? <FinalText state={state} prefix={prefix} fade={turn.live} /> : null}
        {state.finalized &&
        state.replaced &&
        (outcome === "answered" || outcome === "not_found") ? (
          <p className="text-sm text-ink-muted">{tanswer("replacedNote")}</p>
        ) : null}
        {outcome === "search_only" && state.phase === "done" && state.citations.length === 0 ? (
          <p>{t("searchOnly.none")}</p>
        ) : null}
        {state.withheld ? (
          <Alert tone="info" title={tanswer("withheldTitle")}>
            {tanswer("withheldBody")}
          </Alert>
        ) : null}
        {state.phase === "incomplete" ? (
          <Alert tone="info" title={tanswer("incompleteTitle")}>
            {tanswer("incompleteBody")}
          </Alert>
        ) : null}
        <Sources state={state} prefix={prefix} />
        {state.phase === "stopped" ? (
          <Alert tone="info">{stoppedPreview ? ta("stoppedNote") : tanswer("stoppedNote")}</Alert>
        ) : null}
        {state.phase === "interrupted" ? (
          <Alert tone="warning" title={tanswer("interruptedTitle")}>
            {tanswer("interruptedBody")}
          </Alert>
        ) : null}
        {state.phase === "failed" ? (
          <ApiErrorAlert error={askError(state.error, "conversation_not_found")} namespace="ask" />
        ) : null}
        {state.phase === "interrupted" || state.phase === "failed" ? (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => handlers.onRetry(turn)}
            disabled={handlers.busy}
          >
            <Icon name="refresh" className="size-4" />
            {ta("retry")}
          </Button>
        ) : null}
        <ActionsRow turn={turn} handlers={handlers} outcome={outcome} />
        {finished ? <MemoryNotes memory={state.memory} /> : null}
        {turn.latest && state.phase === "done" && state.followups.length > 0 && !handlers.busy ? (
          <div
            role="group"
            aria-label={ta("followupsLabel")}
            className="chat-stagger flex flex-wrap gap-2 pt-1"
          >
            {state.followups.map((question) => (
              <button
                key={question}
                type="button"
                onClick={() => handlers.onFollowUp(question)}
                className="inline-flex min-h-9 max-w-full items-center gap-2 rounded-full border border-border-soft bg-surface px-3.5 py-1.5 text-start text-sm text-ink hover:border-primary hover:bg-primary-soft hover:text-primary motion-safe:hover:transition-colors"
              >
                <Icon name="cornerDownRight" className="size-4 shrink-0 text-ink-subtle" />
                <span className="break-anywhere">{question}</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </article>
  );
}

/**
 * The actions under an answer (copy, ask again, helpful / not helpful, save as verified, the
 * version switcher). Always on the latest answer and on touch screens; on older ones they
 * appear on hover and keyboard focus. While the answer streams the row keeps its height but
 * stays hidden, so nothing jumps when it appears.
 */
function ActionsRow({
  turn,
  handlers,
  outcome,
}: {
  turn: TurnModel;
  handlers: TurnHandlers;
  outcome: ReturnType<typeof outcomeOf>;
}) {
  const t = useTranslations("ask");
  const ta = useTranslations("ask.actions");
  const { state } = turn;
  const queryId = state.queryId;
  const streaming = state.phase === "waiting" || state.phase === "streaming";
  if (state.phase === "failed" || state.phase === "interrupted") return null;
  const copyable = state.text !== "" && (outcome === "answered" || outcome === "pending");
  return (
    <div
      role="group"
      aria-label={ta("label")}
      aria-hidden={streaming || undefined}
      className={cn(
        "chat-actions -ms-1.5 flex min-h-8 flex-wrap items-center gap-0.5",
        streaming && "chat-actions-idle",
        !turn.latest &&
          "opacity-0 group-hover/turn:opacity-100 group-focus-within/turn:opacity-100 [@media(hover:none)]:opacity-100",
      )}
      data-print="hide"
    >
      {streaming ? null : (
        <>
          {copyable ? (
            <CopyButton
              label={ta("copy")}
              doneLabel={ta("copied")}
              text={() =>
                copyText(
                  state.text,
                  state.citations,
                  ta("sourcesHeading"),
                  t("answer.untitled"),
                  (page) => t("answer.page", { page }),
                )
              }
            />
          ) : null}
          {queryId && turn.latest && !handlers.busy ? (
            <ActionButton
              icon="refresh"
              label={ta("regenerate")}
              onClick={() => handlers.onRegenerate(queryId, turn.question)}
            />
          ) : null}
          {queryId && state.phase === "done" && outcome !== "error" ? (
            <FeedbackButtons key={queryId} queryId={queryId} initial={turn.feedback} />
          ) : null}
          {turn.versions ? (
            <VersionSwitcher
              index={turn.versions.index}
              total={turn.versions.total}
              onChange={(index) => handlers.onVersion(turn.versions?.group ?? turn.key, index)}
            />
          ) : null}
          {handlers.canVerify &&
          outcome === "answered" &&
          state.phase === "done" &&
          !state.withheld ? (
            <span className="ms-1">
              <VerifiedAnswerDialog
                draft={draftFrom(state, turn.question)}
                triggerLabel={t("verified.save")}
                triggerVariant="ghost"
                triggerSize="sm"
              />
            </span>
          ) : null}
        </>
      )}
    </div>
  );
}

/** One question and its answer. Memoised: only the streaming turn re-renders while it grows. */
export const ChatTurn = memo(function ChatTurn({
  turn,
  handlers,
}: {
  turn: TurnModel;
  handlers: TurnHandlers;
}) {
  const id = turn.state.queryId ? messageAnchor(turn.state.queryId) : undefined;
  return (
    <div
      id={id}
      tabIndex={id ? -1 : undefined}
      className={cn(
        "group/turn space-y-4 scroll-mt-24 focus:outline-none",
        turn.past && "chat-past",
      )}
    >
      <UserMessage turn={turn} handlers={handlers} />
      <AssistantMessage turn={turn} handlers={handlers} />
    </div>
  );
}, sameTurn);

/** Props equality for ChatTurn: the turn's fields (its state by identity) and the handlers. */
export function sameTurn(
  a: { turn: TurnModel; handlers: TurnHandlers },
  b: { turn: TurnModel; handlers: TurnHandlers },
): boolean {
  const x = a.turn;
  const y = b.turn;
  return (
    a.handlers === b.handlers &&
    x.key === y.key &&
    x.question === y.question &&
    x.state === y.state &&
    x.live === y.live &&
    x.latest === y.latest &&
    x.feedback === y.feedback &&
    x.past === y.past &&
    x.versions?.index === y.versions?.index &&
    x.versions?.total === y.versions?.total &&
    x.versions?.group === y.versions?.group
  );
}
