"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "@/i18n/navigation";
import { ApiError } from "@/lib/bff/query";
import {
  applyEvent,
  extrasOf,
  INITIAL_ASK,
  messageFromState,
  type AnswerExtras,
  type AskState,
} from "./answer";
import { useAskContractApi, type AskBody, type ConversationMessage } from "./contract";
import { CONVERSATION_KEYS, updateMessages, upsertConversation } from "./conversations";
import { MEMORY_KEYS } from "./memory";
import { createSseParser } from "./sse";

/**
 * The Ask chat's streaming controller (FR-KB-008, FR-KB-012), mounted once by the chat layout
 * so an answer keeps streaming while the URL changes from `/ask` to `/ask/c/{id}` (the new
 * conversation's `meta`) and while the reader switches between conversations. Leaving the Ask
 * chat unmounts it, which stops the answer (the API records it `cancelled`).
 *
 * One answer streams at a time. When it ends it is written into the conversation's cached
 * messages (TanStack Query), so the thread shows it without a refetch; what only the stream
 * knew (steps, time taken, search-only reason) is kept beside it for this page view.
 */

export interface LiveTurn {
  /** Local key until the API names the question (`meta.query_id`). */
  key: string;
  /** The conversation it was asked in (null: a new chat). */
  origin: string | null;
  /** The conversation it belongs to (from `meta` for a new chat). */
  conversationId: string | null;
  question: string;
  state: AskState;
  /** performance.now() when it was sent (for "Worked for 4s"). */
  startedAt: number;
  /** The answer this one replaces (regenerate or edit), shown as its earlier version. */
  replaces: string | null;
}

export interface AskOptions {
  conversationId: string | null;
  regenerateOf?: string;
  editOf?: string;
}

/** The last answer that ended and joined its conversation (for the status announcement). */
export interface FinishedTurn {
  key: string;
  conversationId: string;
  state: AskState;
}

export interface ChatController {
  live: LiveTurn | null;
  /** The conversation a new chat (`/ask`) became, until the URL follows it. */
  newChatId: string | null;
  finished: FinishedTurn | null;
  busy: boolean;
  ask: (question: string, options: AskOptions) => void;
  /** Stop reading the answer (aborts the request through the BFF to the API). */
  stop: () => void;
  /** Forget a failed or cut-off turn shown under a conversation. */
  dismissLive: () => void;
  extras: (queryId: string) => AnswerExtras | null;
}

const ChatContext = createContext<ChatController | null>(null);

export function useChat(): ChatController {
  const chat = useContext(ChatContext);
  if (!chat) throw new Error("useChat needs <AskChatProvider> (the Ask chat layout).");
  return chat;
}

/** Window event: "New chat" was chosen (sidebar, shortcut); the new-chat view resets. */
export const NEW_CHAT_EVENT = "sos:ask-new-chat";

/** requestAnimationFrame, or a short timer where there is none (background tabs, test DOMs). */
function nextFrame(callback: () => void): () => void {
  if (typeof requestAnimationFrame === "function") {
    const handle = requestAnimationFrame(() => callback());
    return () => cancelAnimationFrame(handle);
  }
  const handle = setTimeout(callback, 16);
  return () => clearTimeout(handle);
}

function asProblem(error: unknown): { code?: string } {
  return error && typeof error === "object" ? (error as { code?: string }) : {};
}

function markSuperseded(messages: ConversationMessage[], queryId: string, value: boolean) {
  return messages.map((m) => (m.query_id === queryId ? { ...m, superseded: value } : m));
}

export function AskChatProvider({ children }: { children: ReactNode }) {
  const api = useAskContractApi();
  const client = useQueryClient();
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const pathRef = useRef(pathname);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [newChatId, setNewChatId] = useState<string | null>(null);
  const [finished, setFinished] = useState<FinishedTurn | null>(null);
  const liveRef = useRef<LiveTurn | null>(null);
  useEffect(() => {
    liveRef.current = live;
  }, [live]);
  const current = useRef<{
    controller: AbortController;
    stopped: boolean;
    reader: ReadableStreamDefaultReader<Uint8Array> | null;
  } | null>(null);
  const extrasRef = useRef(new Map<string, AnswerExtras>());

  // Once the URL has left `/ask` (it follows the new conversation), a later visit to `/ask`
  // is a fresh new chat.
  const [lastPath, setLastPath] = useState(pathname);
  if (pathname !== lastPath) {
    setLastPath(pathname);
    if (pathname !== "/ask") setNewChatId(null);
  }
  useEffect(() => {
    pathRef.current = pathname;
  }, [pathname]);

  const stop = useCallback(() => {
    const run = current.current;
    if (!run) return;
    run.stopped = true;
    run.controller.abort();
    void run.reader?.cancel().catch(() => undefined);
  }, []);

  // Leaving the Ask chat stops the answer.
  useEffect(() => stop, [stop]);

  // "New chat" (sidebar, Alt+N): the new-chat view starts over. An answer that already has
  // its conversation keeps streaming there; one that does not yet is stopped.
  useEffect(() => {
    const reset = () => {
      setNewChatId(null);
      setFinished(null);
      if (liveRef.current && liveRef.current.conversationId === null) {
        stop();
        setLive(null);
      }
    };
    window.addEventListener(NEW_CHAT_EVENT, reset);
    return () => window.removeEventListener(NEW_CHAT_EVENT, reset);
  }, [stop]);

  const ask = useCallback(
    (question: string, options: AskOptions) => {
      stop();
      const run = {
        controller: new AbortController(),
        stopped: false,
        reader: null as ReadableStreamDefaultReader<Uint8Array> | null,
      };
      current.current = run;
      const replaces = options.regenerateOf ?? options.editOf ?? null;
      let turn: LiveTurn = {
        key: crypto.randomUUID(),
        origin: options.conversationId,
        conversationId: options.conversationId,
        question,
        state: { ...INITIAL_ASK, phase: "waiting" },
        startedAt: performance.now(),
        replaces,
      };
      setLive(turn);
      setFinished(null);
      // The replaced answer becomes an earlier version at once (undone if the ask fails).
      if (replaces && options.conversationId) {
        updateMessages(client, options.conversationId, (m) => markSuperseded(m, replaces, true));
      }
      const unsupersede = () => {
        if (replaces && options.conversationId) {
          updateMessages(client, options.conversationId, (m) => markSuperseded(m, replaces, false));
        }
      };
      // Events are folded as they arrive, but painted at most once per animation frame: only
      // the streaming message re-renders, never once per token (docs/17 §5.3).
      let cancelPaint: (() => void) | null = null;
      const paint = () => {
        cancelPaint = null;
        if (current.current === run) setLive(turn);
      };
      const publish = (state: AskState) => {
        turn = { ...turn, state };
        cancelPaint ??= nextFrame(paint);
      };
      const flush = () => {
        cancelPaint?.();
        cancelPaint = null;
      };

      /** A new chat got its conversation: list it, seed its messages, move to its URL. */
      const adopt = (state: AskState) => {
        const id = state.conversationId;
        if (!id || turn.conversationId === id) return;
        turn = { ...turn, conversationId: id };
        const now = new Date().toISOString();
        const summary = {
          id,
          title: state.title ?? null,
          pinned: false,
          created_at: now,
          updated_at: now,
          message_count: 0,
          version: 0,
        };
        upsertConversation(client, summary);
        if (!client.getQueryData(CONVERSATION_KEYS.detail(id))) {
          client.setQueryData(CONVERSATION_KEYS.detail(id), { ...summary, messages: [] });
        }
        if (turn.origin === null) {
          setNewChatId(id);
          if (pathRef.current === "/ask") router.replace(`/ask/c/${id}`, { scroll: false });
        }
      };

      /** The answer ended: write it into the conversation and refresh the list. */
      const commit = (state: AskState) => {
        const id = turn.conversationId;
        const message = messageFromState(state, question, new Date().toISOString());
        if (state.memory.length > 0) void client.invalidateQueries({ queryKey: MEMORY_KEYS.items });
        if (!id || !message) return false;
        const latency = state.latencyMs ?? Math.round(performance.now() - turn.startedAt);
        extrasRef.current.set(message.query_id, { ...extrasOf(state), latencyMs: latency });
        updateMessages(client, id, (messages) => [
          ...messages.filter((m) => m.query_id !== message.query_id),
          message,
        ]);
        void client.invalidateQueries({ queryKey: CONVERSATION_KEYS.list });
        return true;
      };

      void (async () => {
        let state = turn.state;
        const body: AskBody = {
          question,
          ...(options.conversationId ? { conversation_id: options.conversationId } : {}),
          ...(options.regenerateOf ? { regenerate_of: options.regenerateOf } : {}),
          ...(options.editOf ? { edit_of: options.editOf } : {}),
        };
        try {
          const response = await api.ask(body, run.controller.signal);
          if (current.current !== run) return;
          if (!response.ok || !response.body) {
            let problem: { code?: string } = {};
            try {
              problem = asProblem(await response.json());
            } catch {
              problem = {};
            }
            unsupersede();
            state = {
              ...state,
              phase: "failed",
              error: new ApiError(response.status, problem.code, problem),
            };
            turn = { ...turn, state };
            if (current.current === run) {
              current.current = null;
              setLive(turn);
            }
            return;
          }
          const reader = response.body.getReader();
          run.reader = reader;
          const decoder = new TextDecoder();
          const parser = createSseParser();
          for (;;) {
            const { done, value } = await reader.read();
            if (current.current !== run) return;
            if (done) break;
            const messages = parser.push(decoder.decode(value, { stream: true }));
            if (messages.length === 0) continue;
            state = messages.reduce(applyEvent, state);
            adopt(state);
            publish(state);
          }
          parser.push(decoder.decode());
          parser.end();
          if (state.phase !== "done")
            state = { ...state, phase: run.stopped ? "stopped" : "interrupted" };
        } catch (failure) {
          if (current.current !== run) return;
          if (run.stopped) state = { ...state, phase: "stopped" };
          else if (run.reader) state = { ...state, phase: "interrupted" };
          else {
            unsupersede();
            state = { ...state, phase: "failed", error: failure };
          }
        }
        flush();
        if (current.current !== run) return;
        current.current = null;
        turn = { ...turn, state };
        // Done or stopped answers join the conversation; a cut-off or failed one stays live
        // (with Retry) under it.
        if ((state.phase === "done" || state.phase === "stopped") && commit(state)) {
          setFinished({ key: turn.key, conversationId: turn.conversationId ?? "", state });
          setLive(null);
          return;
        }
        setLive(turn);
      })();
    },
    [api, client, router, stop],
  );

  const dismissLive = useCallback(() => {
    stop();
    setLive(null);
  }, [stop]);

  const extras = useCallback((queryId: string) => extrasRef.current.get(queryId) ?? null, []);

  const busy =
    live !== null && (live.state.phase === "waiting" || live.state.phase === "streaming");
  const value = useMemo<ChatController>(
    () => ({ live, newChatId, finished, busy, ask, stop, dismissLive, extras }),
    [live, newChatId, finished, busy, ask, stop, dismissLive, extras],
  );
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}
