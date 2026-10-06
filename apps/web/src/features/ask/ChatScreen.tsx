"use client";

import { useQuery } from "@tanstack/react-query";
import type { SchoolChoices } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Link, useRouter } from "@/i18n/navigation";
import { containsAadhaarNumber } from "@/lib/aadhaar";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { cn } from "@/lib/cn";
import { text as textSchema } from "@/lib/validation";
import { outcomeOf, stateFromMessage, type AnswerExtras, type AskState } from "./answer";
import { NEW_CHAT_EVENT, useChat, type LiveTurn } from "./chat";
import { Composer, MAX_QUESTION, type ComposerHandle } from "./Composer";
import { ConversationMenu } from "./ConversationMenu";
import { toPlainText } from "./markdown-parse";
import { titleOf, useConversation } from "./conversations";
import { ASK_PERM, type ConversationMessage } from "./data";
import { memoryOn, useMemorySettings } from "./memory";
import { scrollBehavior } from "./motion";
import { AskTabs } from "./parts";
import { useStickToBottom } from "./scroll";
import { currentStep, stepLabel } from "./Status";
import { ChatTurn, type TurnHandlers, type TurnModel } from "./Turn";

const questionSchema = textSchema(MAX_QUESTION);

/** Example questions (i18n) offered as cards in an empty chat; they only fill the box. */
const EXAMPLES = ["exams", "circular", "classCount", "certificate"] as const;

/** The newest turns rendered at first; "Show earlier messages" adds more (caps the DOM). */
export const INITIAL_TURNS = 30;

/** First name for the greeting ("Lakshmi Devi" → "Lakshmi"). */
function firstName(name: string | null | undefined): string | null {
  const first = name?.trim().split(/\s+/u)[0];
  return first ? first : null;
}

function greetingKey(hour: number): "greetingMorning" | "greetingAfternoon" | "greetingEvening" {
  if (hour < 12) return "greetingMorning";
  if (hour < 17) return "greetingAfternoon";
  return "greetingEvening";
}

interface Group {
  key: string;
  versions: Array<{ message: ConversationMessage } | { live: LiveTurn }>;
}

/**
 * The thread's turns: stored messages grouped into versions (a regenerated or edited question
 * supersedes the one before it, which stays as an earlier version), plus the live turn.
 */
const stored = new WeakMap<ConversationMessage, { extras: AnswerExtras | null; state: AskState }>();

/**
 * A stored message's state, cached per message object (the query cache keeps them stable), so
 * a turn that did not change keeps the same state and its memoised component does not
 * re-render while another answer streams.
 */
function storedState(
  message: ConversationMessage,
  extras: (queryId: string) => AnswerExtras | null,
): AskState {
  const extra = extras(message.query_id);
  const hit = stored.get(message);
  if (hit && hit.extras === extra) return hit.state;
  const state = stateFromMessage(message, extra);
  stored.set(message, { extras: extra, state });
  return state;
}

export function buildGroups(
  messages: readonly ConversationMessage[],
  live: LiveTurn | null,
): Group[] {
  const groups: Group[] = [];
  let pending: ConversationMessage[] = [];
  const liveId = live?.state.queryId ?? null;
  for (const message of messages) {
    if (liveId && message.query_id === liveId) continue;
    if (message.superseded) {
      pending.push(message);
      continue;
    }
    const versions = [...pending, message].map((m) => ({ message: m }));
    groups.push({ key: versions[0]?.message.query_id ?? message.query_id, versions });
    pending = [];
  }
  if (live) {
    const versions = [...pending.map((m) => ({ message: m })), { live }];
    const first = versions[0];
    groups.push({
      key: first && "message" in first ? first.message.query_id : live.key,
      versions,
    });
  } else if (pending.length > 0) {
    // Earlier versions whose replacement is not stored yet: show the newest of them.
    groups.push({
      key: pending[0]?.query_id ?? "pending",
      versions: pending.map((m) => ({ message: m })),
    });
  }
  return groups;
}

function hashTarget(): string | null {
  if (typeof window === "undefined") return null;
  const hash = window.location.hash.slice(1);
  return hash.startsWith("m-") ? hash : null;
}

/**
 * Ask the school as a chat (US-801..803, FR-KB-001..012): the conversation (`/ask/c/{id}`) or
 * a new chat (`/ask`), the streamed answers with their sources, and the composer. See
 * docs/17 §5.3 for the anatomy, motion and accessibility.
 */
export function ChatScreen({ conversationId }: { conversationId: string | null }) {
  const t = useTranslations("ask");
  const tc = useTranslations("common");
  const tv = useTranslations("validation");
  const ts = useTranslations("ask.steps");
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const chat = useChat();
  const router = useRouter();
  const api = useBffClient("staff");
  const composer = useRef<ComposerHandle>(null);
  const [logNode, setLogNode] = useState<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | undefined>(undefined);
  const [selected, setSelected] = useState<Record<string, number>>({});
  const [shown, setShown] = useState(INITIAL_TURNS);
  const allowed = can(ASK_PERM.ask);

  const { live, finished, busy, ask, dismissLive, extras } = chat;
  // A new chat follows its conversation once the answer's `meta` names it.
  const id = conversationId ?? chat.newChatId;
  // The thread's identity: a new chat becoming its conversation (and the URL following it)
  // is the same thread; opening another conversation is a new one (crossfade, own scroll,
  // versions and "show earlier" reset).
  const [previousId, setPreviousId] = useState(id);
  const [viewKey, setViewKey] = useState(id ?? "new");
  if (id !== previousId) {
    setPreviousId(id);
    if (!(previousId === null && id !== null && id === chat.newChatId)) {
      setViewKey(id ?? "new");
      setSelected({});
      setShown(INITIAL_TURNS);
    }
  }
  const liveHere =
    live !== null &&
    (id !== null
      ? live.conversationId === id
      : live.origin === null && live.conversationId === null);

  // "New chat" (sidebar, Alt+N) while already on /ask: start over here.
  useEffect(() => {
    const reset = () => {
      setSelected({});
      setError(undefined);
      composer.current?.focus();
    };
    window.addEventListener(NEW_CHAT_EVENT, reset);
    return () => window.removeEventListener(NEW_CHAT_EVENT, reset);
  }, []);

  const detail = useConversation(allowed ? id : null);
  const memory = useMemorySettings(allowed);
  const schools = useQuery({
    queryKey: ["staff", "me", "schools"],
    queryFn: () => unwrap(api.GET("/api/v1/me/schools")) as Promise<SchoolChoices>,
    enabled: allowed && id === null,
    staleTime: 300_000,
    retry: false,
  });

  const groups = useMemo(
    () => buildGroups(detail.data?.messages ?? [], liveHere ? live : null),
    [detail.data?.messages, live, liveHere],
  );

  const turns = useMemo<TurnModel[]>(() => {
    const all = groups.map((group, g): TurnModel => {
      const count = group.versions.length;
      const index = Math.min(selected[group.key] ?? count - 1, count - 1);
      const version = group.versions[index] ?? group.versions[count - 1];
      const latest = g === groups.length - 1;
      if (version && "live" in version) {
        return {
          key: version.live.state.queryId ?? version.live.key,
          question: version.live.question,
          state: version.live.state,
          live: true,
          latest,
          feedback: null,
          versions: count > 1 ? { index, total: count, group: group.key } : null,
          past: false,
        };
      }
      const message = (version as { message: ConversationMessage }).message;
      return {
        key: message.query_id,
        question: message.question,
        state: storedState(message, extras),
        live: false,
        latest,
        feedback: message.feedback,
        versions: count > 1 ? { index, total: count, group: group.key } : null,
        past: g < groups.length - 3,
      };
    });
    return all;
  }, [groups, selected, extras]);

  const visible = turns.slice(Math.max(0, turns.length - shown));
  const hidden = turns.length - visible.length;
  const empty = turns.length === 0 && !(id !== null && detail.isPending);
  const ready = !detail.isPending || id === null;

  const stick = useStickToBottom(logNode, viewKey, ready && turns.length > 0);
  const { onSent } = stick;

  // A link to one message (`#m-{query id}`, a past-chat source) scrolls to it once it is on
  // the page, focuses it and highlights it briefly (a still tint under reduced motion).
  const loaded = detail.data !== undefined;
  useEffect(() => {
    if (!loaded) return;
    const target = hashTarget();
    const node = target ? document.getElementById(target) : null;
    if (!node) return;
    node.classList.add("chat-highlight");
    node.scrollIntoView?.({ behavior: scrollBehavior(), block: "center" });
    node.focus({ preventScroll: true });
  }, [loaded, viewKey]);

  // A failed ask puts the question back in the box (the draft is never lost).
  const failedKey = live?.state.phase === "failed" ? live.key : null;
  useEffect(() => {
    if (!failedKey || !live) return;
    if (!composer.current?.getText()) composer.current?.setText(live.question);
    // Only when a new failure appears.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [failedKey]);

  const send = useCallback(
    (question: string, options: { regenerateOf?: string; editOf?: string } = {}): string | null => {
      const parsed = questionSchema.safeParse(question);
      if (!parsed.success) {
        return tv(parsed.error.issues[0]?.message === "tooLong" ? "tooLong" : "required");
      }
      // Invariant 4: a full Aadhaar number never leaves the browser (the API masks it too).
      if (containsAadhaarNumber(parsed.data)) return tv("noAadhaar");
      ask(parsed.data, { conversationId: id, ...options });
      onSent();
      return null;
    },
    [ask, id, onSent, tv],
  );

  const handlers = useMemo<TurnHandlers>(
    () => ({
      busy,
      canVerify: can(ASK_PERM.manageVerified),
      onVersion: (group, index) => setSelected((current) => ({ ...current, [group]: index })),
      onRegenerate: (queryId, question) => {
        setSelected({});
        send(question, { regenerateOf: queryId });
      },
      onEdit: (queryId, question) => {
        setSelected({});
        return send(question, { editOf: queryId });
      },
      onRetry: (turn) => {
        dismissLive();
        const queryId = turn.state.queryId;
        send(
          turn.question,
          queryId && turn.state.phase === "interrupted" ? { regenerateOf: queryId } : {},
        );
      },
      onFollowUp: (question) => {
        send(question);
      },
    }),
    [busy, dismissLive, can, send],
  );

  // Keyboard: "/" moves to the question box (when not typing elsewhere); Alt+N starts a new chat.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return;
      const target = event.target as HTMLElement | null;
      const typing =
        target?.closest("input, textarea, select, [contenteditable='true'], dialog[open]") !==
          null && target !== null;
      if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !typing) {
        event.preventDefault();
        composer.current?.focus();
        return;
      }
      if (
        event.altKey &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.shiftKey &&
        event.code === "KeyN"
      ) {
        event.preventDefault();
        window.dispatchEvent(new Event(NEW_CHAT_EVENT));
        if (conversationId !== null) router.push("/ask");
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [conversationId, router]);

  if (me.isPending) return <LoadingState label={tc("loading")} />;
  if (!allowed) {
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

  const notFound = detail.error instanceof ApiError && detail.error.status === 404;
  const title = titleOf(detail.data) ?? (id ? t("chat.untitled") : null);
  const announce = liveAnnouncement(
    liveHere
      ? (live?.state ?? null)
      : finished && finished.conversationId === id
        ? finished.state
        : null,
    t,
    ts,
  );

  const name = firstName(me.data?.display_name);
  const school =
    schools.data?.data.find((choice) => choice.tenant_id === me.data?.tenant_id)?.name ?? null;
  const greeting = name
    ? t(`chat.${greetingKey(new Date().getHours())}`, { name })
    : t("chat.greetingPlain");

  const memoryTool =
    memory.data && memoryOn(memory.data) ? (
      <Link
        href="/ask/memory"
        className="group/mem relative inline-flex min-h-8 items-center gap-1.5 rounded-full border border-border px-2.5 text-xs font-semibold text-violet-ink hover:bg-violet-soft"
        aria-describedby="ask-memory-tip"
      >
        <Icon name="memory" className="size-4" />
        <span>{t("memory.indicator")}</span>
        <span
          id="ask-memory-tip"
          role="tooltip"
          className="pointer-events-none absolute bottom-full start-0 mb-2 hidden w-64 rounded-md bg-action px-3 py-2 text-start text-xs font-normal text-on-action shadow-popover group-hover/mem:block group-focus-visible/mem:block"
        >
          {t("memory.indicatorTip")} {t("memory.manage")}
        </span>
      </Link>
    ) : null;

  const composerNode = (
    <Composer
      ref={composer}
      busy={chat.busy}
      large={empty}
      placeholder={empty ? t("composer.placeholder") : t("composer.placeholderFollowUp")}
      error={error}
      tools={memoryTool}
      onTextChange={() => setError(undefined)}
      onStop={chat.stop}
      onSubmit={(question) => {
        const failure = send(question);
        setError(failure ?? undefined);
        if (failure) composer.current?.focus();
        return failure === null;
      }}
    />
  );

  return (
    <div className="chat-fade chat-canvas relative -mb-6 flex flex-col rounded-xl border border-border bg-surface shadow-card">
      {/* Header: the chat's title (the page's h1), its options, and the Ask sections. */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-3 sm:px-6">
        <h1 className="min-w-0 flex-1 text-base font-semibold break-anywhere text-ink">
          {title ?? t("title")}
        </h1>
        <ConversationMenu conversation={detail.data ?? null} />
        <AskTabs active="ask" />
      </header>

      {/* Announcements: steps (sparingly) and the finished answer, never each word. */}
      <div role="status" aria-live="polite" className="sr-only">
        {announce.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>

      <div
        className={cn(
          "mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 sm:px-6",
          empty && "justify-center py-8",
        )}
      >
        {/* The thread crossfades when another conversation opens; the composer below stays. */}
        <div key={viewKey} className={cn("chat-fade flex flex-col", !empty && "flex-1")}>
          {notFound ? (
            <div className="py-10">
              <EmptyState
                icon="message"
                title={t("chat.notFoundTitle")}
                body={t("chat.notFoundBody")}
                action={<ButtonLink href="/ask">{t("chat.newChat")}</ButtonLink>}
              />
            </div>
          ) : detail.error ? (
            <div className="py-6">
              <ApiErrorAlert error={detail.error} namespace="ask" />
            </div>
          ) : empty ? (
            <div className="chat-enter space-y-2 pb-6 text-center">
              <span
                aria-hidden="true"
                className="ai-gradient mx-auto mb-4 flex size-12 items-center justify-center rounded-full text-white"
              >
                <Icon name="sparkles" className="size-6" />
              </span>
              <p className="text-2xl font-semibold text-ink sm:text-3xl">{greeting}</p>
              <p className="mx-auto max-w-xl text-ink-muted">
                {school ? t("chat.introSchool", { school }) : t("chat.introPlain")}
              </p>
            </div>
          ) : id !== null && detail.isPending ? (
            <div className="py-8">
              <LoadingState label={t("chat.loading")} rows={3} />
            </div>
          ) : (
            <div
              ref={setLogNode}
              role="log"
              aria-live="off"
              aria-label={t("chat.logLabel")}
              className="flex-1 space-y-8 py-6"
            >
              {hidden > 0 ? (
                <div className="flex justify-center">
                  <button
                    type="button"
                    onClick={() => setShown((count) => count + INITIAL_TURNS)}
                    className="inline-flex min-h-9 items-center gap-2 rounded-full border border-border-soft px-4 text-sm font-semibold text-ink hover:bg-surface-muted"
                  >
                    <Icon name="arrowUp" className="size-4" />
                    {t("chat.showEarlier")}
                  </button>
                </div>
              ) : null}
              {visible.map((turn, i) => (
                <div key={turn.key} className="space-y-8">
                  {turn.state.summarized && (i > 0 || hidden > 0) ? (
                    <p className="flex items-center gap-3 text-xs text-ink-subtle">
                      <span aria-hidden="true" className="h-px flex-1 bg-border" />
                      {t("chat.summarized")}
                      <span aria-hidden="true" className="h-px flex-1 bg-border" />
                    </p>
                  ) : null}
                  <ChatTurn turn={turn} handlers={handlers} />
                </div>
              ))}
            </div>
          )}
        </div>

        {/* The composer stays mounted in the same place (focus and text survive the first
            question); it docks to the bottom once the chat has messages. */}
        <div
          className={cn(
            empty
              ? "relative"
              : "chat-dock sticky bottom-0 z-10 -mx-4 px-4 pt-6 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:-mx-6 sm:px-6",
          )}
        >
          {!stick.pinned && !empty ? (
            <div className="pointer-events-none absolute inset-x-0 -top-4 flex justify-center">
              <button
                type="button"
                onClick={stick.jump}
                className="chat-pop pointer-events-auto relative inline-flex min-h-9 items-center gap-2 rounded-full border border-border-soft bg-surface px-4 text-sm font-semibold text-ink shadow-popover hover:bg-surface-muted"
              >
                <Icon name="arrowDown" className="size-4" />
                {stick.unread ? t("chat.jumpToLatestUnread") : t("chat.jumpToLatest")}
                {stick.unread ? (
                  <span
                    aria-hidden="true"
                    className="absolute -top-0.5 -right-0.5 size-2.5 rounded-full border-2 border-surface bg-primary"
                  />
                ) : null}
              </button>
            </div>
          ) : null}
          {composerNode}
        </div>

        {empty ? (
          <div className="pt-6">
            <p id="ask-examples" className="sr-only">
              {t("chat.suggestionsLabel")}
            </p>
            <ul aria-labelledby="ask-examples" className="chat-stagger grid gap-2 sm:grid-cols-2">
              {EXAMPLES.map((key) => (
                <li key={key} className="flex">
                  <button
                    type="button"
                    onClick={() => {
                      composer.current?.setText(t(`examples.${key}`));
                      setError(undefined);
                    }}
                    className="flex w-full items-start gap-3 rounded-xl border border-border bg-surface p-3.5 text-start text-sm text-ink shadow-raised hover:border-primary hover:bg-primary-soft motion-safe:hover:transition-colors"
                  >
                    <Icon name="message" className="mt-0.5 size-4 shrink-0 text-primary" />
                    <span>{t(`examples.${key}`)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** What the status region says: the current step (no counts), then the finished answer. */
function liveAnnouncement(
  state: AskState | null,
  t: ReturnType<typeof useTranslations<"ask">>,
  ts: ReturnType<typeof useTranslations<"ask.steps">>,
): string[] {
  if (!state) return [];
  switch (state.phase) {
    case "waiting":
    case "streaming": {
      const step = currentStep(state);
      return step ? [stepLabel(step, ts, false)] : [];
    }
    case "done": {
      if (outcomeOf(state) === "error") return [t("status.failed")];
      // The checked answer is read once, as plain text (never each streamed word).
      const known = new Set(state.citations.map((c) => c.index));
      const answer = outcomeOf(state) === "answered" ? toPlainText(state.text, known, false) : "";
      return answer ? [t("status.done"), answer] : [t("status.done")];
    }
    case "stopped":
      return [t("status.stopped")];
    case "interrupted":
      return [t("status.interrupted")];
    case "failed":
      return [t("status.failed")];
    default:
      return [];
  }
}
