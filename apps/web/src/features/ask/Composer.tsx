"use client";

import { useTranslations } from "next-intl";
import {
  useCallback,
  useId,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
} from "react";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

/** The API's limit for a question (docs/09 Knowledge). */
export const MAX_QUESTION = 1000;
/** The counter appears from here on. */
export const COUNTER_FROM = 800;
/** A pointer click on the button this soon after sending is a double click, not Stop. */
const STOP_GUARD_MS = 400;

export interface ComposerHandle {
  focus: () => void;
  /** Replace the text (an example question, a restored draft) and focus the box. */
  setText: (text: string) => void;
  getText: () => string;
}

function onlineSnapshot(): boolean {
  return typeof navigator === "undefined" || navigator.onLine !== false;
}

function subscribeOnline(onChange: () => void): () => void {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

/** navigator.onLine, live (server snapshot: online). */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribeOnline, onlineSnapshot, () => true);
}

/** Phones and tablets without a mouse: Enter adds a line, the button sends. */
function touchFirst(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(pointer: coarse)").matches &&
    !window.matchMedia("(any-pointer: fine)").matches
  );
}

/**
 * Whether this Enter keydown should send the question: never while an input method is
 * composing (Telugu, and any IME: `isComposing`, or keyCode 229 which Safari and some Android
 * keyboards report for the Enter that commits the text), never with Shift (a new line); on
 * touch-first devices only with Ctrl/Cmd (the on-screen Enter adds a line).
 */
export function shouldSend(
  event: Pick<
    KeyboardEvent<HTMLTextAreaElement>,
    "key" | "shiftKey" | "ctrlKey" | "metaKey" | "altKey"
  > & {
    nativeEvent: { isComposing?: boolean; keyCode?: number };
  },
  composing: boolean,
  touch: boolean,
): boolean {
  if (event.key !== "Enter") return false;
  if (composing || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return false;
  if (event.shiftKey || event.altKey) return false;
  if (event.ctrlKey || event.metaKey) return true;
  return !touch;
}

/**
 * The question box (FR-KB-008, NFR-A11Y-001): grows with the text up to a limit, then scrolls;
 * Enter sends and Shift+Enter adds a line (IME-safe: never sends while composing Telugu);
 * the send button turns into Stop while an answer streams (a double click never stops it);
 * a character counter near the limit; offline and busy states say what to do.
 */
export function Composer({
  ref,
  busy,
  onSubmit,
  onStop,
  error,
  placeholder,
  tools,
  large = false,
  onTextChange,
}: {
  ref?: Ref<ComposerHandle>;
  busy: boolean;
  /** Returns false when the question was not sent (the text then stays). */
  onSubmit: (question: string) => boolean;
  onStop: () => void;
  error?: string | undefined;
  placeholder: string;
  /** Extra controls at the start of the toolbar (the memory indicator). */
  tools?: ReactNode;
  /** The centred composer of an empty chat: a little taller. */
  large?: boolean;
  onTextChange?: () => void;
}) {
  const t = useTranslations("ask.composer");
  const online = useOnline();
  const [text, setTextState] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);
  const sentAt = useRef(0);
  const hintId = useId();
  const counterId = useId();
  const errorId = useId();
  const shortcutId = useId();
  const [touch] = useState(touchFirst);

  const setText = useCallback((value: string) => {
    setTextState(value.slice(0, MAX_QUESTION));
  }, []);

  useImperativeHandle(
    ref,
    () => ({
      focus: () => box.current?.focus(),
      setText: (value: string) => {
        setText(value);
        box.current?.focus();
      },
      getText: () => box.current?.value ?? "",
    }),
    [setText],
  );

  // Grow with the text (CSSOM, allowed by the CSP); CSS caps the height and then it scrolls.
  useLayoutEffect(() => {
    const node = box.current;
    if (!node) return;
    node.style.height = "auto";
    if (node.scrollHeight > 0) node.style.height = `${node.scrollHeight}px`;
  }, [text]);

  const send = () => {
    if (busy || !online) return;
    if (onSubmit(text)) {
      sentAt.current = performance.now();
      setTextState("");
    }
  };

  const remaining = MAX_QUESTION - text.length;
  const describedBy = [
    error ? errorId : null,
    hintId,
    text.length >= COUNTER_FROM ? counterId : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <form
      noValidate
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        send();
      }}
      className="space-y-1.5"
      data-print="hide"
    >
      <div
        className={cn(
          "rounded-2xl border border-border-control bg-surface shadow-raised",
          "focus-within:outline-3 focus-within:outline-offset-2 focus-within:outline-focus",
          error && "border-danger",
        )}
      >
        <label htmlFor={`${hintId}-box`} className="sr-only">
          {t("label")}
        </label>
        <textarea
          ref={box}
          id={`${hintId}-box`}
          name="question"
          rows={1}
          value={text}
          maxLength={MAX_QUESTION}
          placeholder={placeholder}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          aria-keyshortcuts="/"
          onChange={(event) => {
            setTextState(event.target.value);
            onTextChange?.();
          }}
          onCompositionStart={() => {
            composing.current = true;
          }}
          onCompositionEnd={() => {
            composing.current = false;
          }}
          onKeyDown={(event) => {
            if (shouldSend(event, composing.current, touch)) {
              event.preventDefault();
              send();
            }
          }}
          className={cn(
            "block max-h-[40vh] w-full resize-none overflow-y-auto bg-transparent px-4 pt-3.5 pb-1 text-base leading-relaxed text-ink placeholder:text-ink-subtle",
            "focus-visible:outline-none",
            large ? "min-h-20" : "min-h-12",
          )}
        />
        <div className="flex items-center gap-2 px-2 pb-2">
          <div className="flex min-w-0 flex-1 items-center gap-2">{tools}</div>
          {text.length >= COUNTER_FROM ? (
            <span
              id={counterId}
              className={cn(
                "shrink-0 text-xs tabular-nums",
                remaining <= 50 ? "font-semibold text-danger" : "text-ink-subtle",
              )}
            >
              {t("remaining", { count: remaining })}
            </span>
          ) : null}
          <button
            type={busy ? "button" : "submit"}
            aria-label={busy ? t("stop") : t("send")}
            aria-disabled={!busy && !online ? true : undefined}
            onClick={(event) => {
              if (!busy) return;
              // The second click of a double click on "Ask" must not stop the new answer.
              if (event.detail > 0 && performance.now() - sentAt.current < STOP_GUARD_MS) return;
              onStop();
              box.current?.focus();
            }}
            className={cn(
              "relative inline-flex size-10 shrink-0 items-center justify-center rounded-full border",
              "border-action bg-action text-on-action shadow-raised hover:bg-action-hover",
              "aria-disabled:cursor-not-allowed aria-disabled:opacity-60",
            )}
          >
            <span
              aria-hidden="true"
              className={cn(
                "chat-morph-icon absolute inset-0 flex items-center justify-center",
                busy ? "scale-50 opacity-0" : "scale-100 opacity-100",
              )}
            >
              <Icon name="arrowUp" className="size-5" />
            </span>
            <span
              aria-hidden="true"
              className={cn(
                "chat-morph-icon absolute inset-0 flex items-center justify-center",
                busy ? "scale-100 opacity-100" : "scale-50 opacity-0",
              )}
            >
              <Icon name="stop" className="size-4 fill-current" />
            </span>
          </button>
        </div>
      </div>
      {error ? (
        <p id={errorId} role="alert" className="px-2 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      {!online ? (
        <p role="status" className="px-2 text-sm font-semibold text-warning-ink">
          {t("offline")}
        </p>
      ) : null}
      <p id={hintId} className="px-2 text-xs text-ink-subtle">
        {touch ? t("hintTouch") : t("hint")}
        <span id={shortcutId} className="max-sm:hidden">{` ${t("shortcuts")}`}</span>
      </p>
    </form>
  );
}
