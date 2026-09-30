"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { scrollBehavior } from "./motion";

/**
 * Scrolling of the Ask chat (docs/17 §5.3). The page itself scrolls (the composer is sticky at
 * the bottom), so the numbers are the window's.
 *
 * - While an answer grows, the view stays pinned to the bottom unless the reader scrolled up.
 * - Scrolled up: a floating "Jump to latest" button appears; it shows a dot when new text
 *   arrived since. Pressing it scrolls down (smoothly, unless reduced motion) and pins again.
 * - Each conversation remembers where it was read (in memory, this page view only).
 */

/** Within this many pixels of the end counts as "at the bottom". */
export const BOTTOM_SLACK = 96;

export interface ScrollMetrics {
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
}

export function isNearBottom(metrics: ScrollMetrics, slack = BOTTOM_SLACK): boolean {
  return metrics.scrollHeight - (metrics.scrollTop + metrics.clientHeight) <= slack;
}

export interface StickState {
  /** Follow new content to the bottom. */
  pinned: boolean;
  /** New content arrived while not pinned (the dot on "Jump to latest"). */
  unread: boolean;
}

export type StickEvent =
  | { type: "scrolled"; nearBottom: boolean; userInitiated: boolean }
  | { type: "grew" }
  | { type: "jumped" }
  | { type: "sent" };

/** The pin/unread state machine (pure, for tests). */
export function stickReducer(state: StickState, event: StickEvent): StickState {
  switch (event.type) {
    case "scrolled":
      if (event.nearBottom) return { pinned: true, unread: false };
      // Only the reader unpins: our own scrolling (and layout shifts) never do.
      return event.userInitiated ? { ...state, pinned: false } : state;
    case "grew":
      return state.pinned ? state : { ...state, unread: true };
    case "jumped":
    case "sent":
      return { pinned: true, unread: false };
  }
}

function windowMetrics(): ScrollMetrics {
  const root = document.scrollingElement ?? document.documentElement;
  return {
    scrollTop: window.scrollY,
    clientHeight: window.innerHeight,
    scrollHeight: root.scrollHeight,
  };
}

function scrollToEnd(behavior: ScrollBehavior): void {
  const root = document.scrollingElement ?? document.documentElement;
  if (typeof window.scrollTo === "function") {
    try {
      window.scrollTo({ top: root.scrollHeight, behavior });
    } catch {
      // Test DOMs without layout: nothing to scroll.
    }
  }
}

/** Remembered scroll positions per conversation (this page view only; never stored). */
const positions = new Map<string, number>();

export interface StickToBottom {
  pinned: boolean;
  unread: boolean;
  /** Scroll to the latest message and follow it again. */
  jump: () => void;
  /** A question was sent: follow the answer. */
  onSent: () => void;
}

/**
 * Keep the chat pinned to the bottom while `node` (the message log) grows, unless the reader scrolled up.
 * `key` names the conversation, for restoring where it was read.
 */
export function useStickToBottom(
  node: HTMLElement | null,
  key: string,
  ready: boolean,
): StickToBottom {
  const [state, setState] = useState<StickState>({ pinned: true, unread: false });
  const stateRef = useRef(state);
  const programmatic = useRef(0);
  const userIntent = useRef(0);

  const dispatch = useCallback((event: StickEvent) => {
    const next = stickReducer(stateRef.current, event);
    if (next.pinned !== stateRef.current.pinned || next.unread !== stateRef.current.unread) {
      stateRef.current = next;
      setState(next);
    }
  }, []);

  const follow = useCallback((behavior: ScrollBehavior) => {
    programmatic.current = performance.now();
    scrollToEnd(behavior);
  }, []);

  // Wheel, touch and keys mark a scroll as the reader's own.
  useEffect(() => {
    const mark = () => {
      userIntent.current = performance.now();
    };
    const onKey = (event: KeyboardEvent) => {
      if (["PageUp", "PageDown", "ArrowUp", "ArrowDown", "Home", "End", " "].includes(event.key)) {
        mark();
      }
    };
    const onScroll = () => {
      const now = performance.now();
      const userInitiated = now - userIntent.current < 800 && now - programmatic.current > 60;
      dispatch({ type: "scrolled", nearBottom: isNearBottom(windowMetrics()), userInitiated });
    };
    window.addEventListener("wheel", mark, { passive: true });
    window.addEventListener("touchmove", mark, { passive: true });
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("wheel", mark);
      window.removeEventListener("touchmove", mark);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll);
    };
  }, [dispatch]);

  // Content grew (a new message, streamed text): follow it when pinned, else mark unread.
  useEffect(() => {
    if (!node || typeof ResizeObserver === "undefined") return;
    let height = node.getBoundingClientRect().height;
    const observer = new ResizeObserver(() => {
      const next = node.getBoundingClientRect().height;
      if (next <= height) {
        height = next;
        return;
      }
      height = next;
      if (stateRef.current.pinned) follow("auto");
      else dispatch({ type: "grew" });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [node, dispatch, follow]);

  // Restore where this conversation was read (or start at the end), then remember on leave.
  useEffect(() => {
    if (!ready) return;
    const saved = positions.get(key);
    programmatic.current = performance.now();
    if (saved !== undefined) {
      try {
        window.scrollTo({ top: saved, behavior: "auto" });
      } catch {
        // No layout (tests).
      }
      const pinned = isNearBottom(windowMetrics());
      stateRef.current = { pinned, unread: false };
      setState(stateRef.current);
    } else {
      follow("auto");
    }
    return () => {
      positions.set(key, window.scrollY);
    };
  }, [key, ready, follow]);

  const jump = useCallback(() => {
    dispatch({ type: "jumped" });
    follow(scrollBehavior());
  }, [dispatch, follow]);

  const onSent = useCallback(() => {
    dispatch({ type: "sent" });
    follow(scrollBehavior());
  }, [dispatch, follow]);

  return { pinned: state.pinned, unread: state.unread, jump, onSent };
}
