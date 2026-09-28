"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ApiError, useBffClient } from "@/lib/bff/query";
import { applyEvent, INITIAL_ASK, type AskState } from "./answer";
import { createSseParser, type SseMessage } from "./sse";

type Action =
  | { type: "start" }
  | { type: "events"; messages: SseMessage[] }
  | { type: "end"; stopped: boolean }
  | { type: "failed"; error: unknown }
  | { type: "reset" };

function reducer(state: AskState, action: Action): AskState {
  switch (action.type) {
    case "start":
      return { ...INITIAL_ASK, phase: "waiting" };
    case "events":
      return action.messages.reduce(applyEvent, state);
    case "end":
      if (state.phase === "done") return state;
      return { ...state, phase: action.stopped ? "stopped" : "interrupted" };
    case "failed":
      return { ...state, phase: "failed", error: action.error };
    case "reset":
      return INITIAL_ASK;
  }
}

export interface AskController {
  state: AskState;
  /** The question of the current answer (kept in memory only, never in the URL or storage). */
  question: string;
  ask: (question: string) => void;
  /** Stop reading the answer (aborts the request through the BFF to the API). */
  stop: () => void;
  busy: boolean;
}

function asProblem(error: unknown): { code?: string } {
  return error && typeof error === "object" ? (error as { code?: string }) : {};
}

/**
 * Ask one question at a time (US-801, FR-KB-008): `POST /bff/api/v1/knowledge/ask` through
 * the typed BFF client (CSRF header, 401 → sign-in, 428 → step-up), reading the SSE body as
 * it arrives. The Ask session id (FR-KB-012) lives for this page view only.
 */
export function useAsk(): AskController {
  const api = useBffClient("staff");
  const [state, dispatch] = useReducer(reducer, INITIAL_ASK);
  const [question, setQuestion] = useState("");
  const [sessionId] = useState(() => crypto.randomUUID());
  const current = useRef<{ controller: AbortController; stopped: boolean } | null>(null);
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);

  const stop = useCallback(() => {
    const run = current.current;
    if (!run) return;
    run.stopped = true;
    run.controller.abort();
    void readerRef.current?.cancel().catch(() => undefined);
  }, []);

  // Leaving the page stops the answer.
  useEffect(() => stop, [stop]);

  const ask = useCallback(
    (text: string) => {
      stop();
      const run = { controller: new AbortController(), stopped: false };
      current.current = run;
      setQuestion(text);
      dispatch({ type: "start" });
      void (async () => {
        let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
        try {
          const { data, error, response } = await api.POST("/api/v1/knowledge/ask", {
            body: { question: text, session_id: sessionId },
            headers: { Accept: "text/event-stream" },
            parseAs: "stream",
            signal: run.controller.signal,
          });
          if (current.current !== run) return;
          if (!response.ok || !data) {
            const problem = asProblem(error);
            dispatch({
              type: "failed",
              error: new ApiError(response.status, problem.code, problem),
            });
            return;
          }
          reader = (data as ReadableStream<Uint8Array>).getReader();
          readerRef.current = reader;
          const decoder = new TextDecoder();
          const parser = createSseParser();
          for (;;) {
            const { done, value } = await reader.read();
            if (current.current !== run) return;
            if (done) break;
            const messages = parser.push(decoder.decode(value, { stream: true }));
            if (messages.length > 0) dispatch({ type: "events", messages });
          }
          parser.push(decoder.decode());
          parser.end();
          dispatch({ type: "end", stopped: run.stopped });
        } catch (failure) {
          if (current.current !== run) return;
          if (run.stopped) dispatch({ type: "end", stopped: true });
          else if (reader) dispatch({ type: "end", stopped: false });
          else dispatch({ type: "failed", error: failure });
        } finally {
          if (readerRef.current === reader) readerRef.current = null;
          if (current.current === run) current.current = null;
        }
      })();
    },
    [api, sessionId, stop],
  );

  const busy = state.phase === "waiting" || state.phase === "streaming";
  return { state, question, ask, stop, busy };
}
