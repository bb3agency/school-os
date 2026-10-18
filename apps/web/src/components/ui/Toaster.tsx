"use client";

import { useTranslations } from "next-intl";
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
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "./Icon";

export type ToastTone = "success" | "info" | "warning";

export interface ToastInput {
  title: string;
  body?: string;
  tone?: ToastTone;
}

interface Toast extends Required<Pick<ToastInput, "title" | "tone">> {
  id: number;
  body?: string;
}

interface ToastApi {
  /** Show a short confirmation of a background result ("Receipt sent on WhatsApp"). */
  show: (toast: ToastInput) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/** Outside a provider (tests, pages outside the console) toasts are silently dropped. */
const NOOP: ToastApi = { show: () => undefined };

export function useToast(): ToastApi {
  return useContext(ToastContext) ?? NOOP;
}

/** How long a toast stays when nothing holds it (hover or focus pause it). */
export const TOAST_MS = 6000;
const MAX_TOASTS = 3;

const tones: Record<ToastTone, { box: string; icon: IconName }> = {
  success: { box: "border-positive-border bg-success-soft text-success-ink", icon: "checkCircle" },
  info: { box: "border-info-border bg-info-soft text-info-ink", icon: "info" },
  warning: { box: "border-warning-border bg-warning-soft text-warning-ink", icon: "alert" },
};

function ToastCard({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  const t = useTranslations("ui.toast");
  const [held, setHeld] = useState(false);
  const timer = useRef<number | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  // Hover or focus inside holds the toast (WCAG 2.2.1: enough time to read it).
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const hold = () => setHeld(true);
    const release = () => setHeld(false);
    box.addEventListener("pointerenter", hold);
    box.addEventListener("pointerleave", release);
    box.addEventListener("focusin", hold);
    box.addEventListener("focusout", release);
    return () => {
      box.removeEventListener("pointerenter", hold);
      box.removeEventListener("pointerleave", release);
      box.removeEventListener("focusin", hold);
      box.removeEventListener("focusout", release);
    };
  }, []);

  useEffect(() => {
    if (held) return;
    timer.current = window.setTimeout(() => onDismiss(toast.id), TOAST_MS);
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [held, onDismiss, toast.id]);

  const tone = tones[toast.tone];
  return (
    <div
      ref={boxRef}
      className={cn(
        "bar-in flex items-start gap-3 rounded-lg border p-3 text-sm shadow-popover",
        tone.box,
      )}
    >
      <Icon name={tone.icon} className="mt-0.5 size-4.5 shrink-0" />
      <div className="min-w-0 flex-1 break-anywhere">
        <p className="font-semibold">{toast.title}</p>
        {toast.body ? <p className="mt-0.5">{toast.body}</p> : null}
      </div>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label={t("dismiss")}
        className="-m-1 inline-flex size-7 shrink-0 items-center justify-center rounded-md hover:bg-surface/60 pointer-coarse:size-11"
      >
        <Icon name="close" className="size-4" />
      </button>
    </div>
  );
}

/**
 * Toasts (owner decision 2026-10-18; docs/17 §5.5): a CSS-only, CSP-safe status region for
 * results of background work. One polite `role="status"` region (always in the DOM, so
 * screen readers hear new toasts), at most three toasts, six seconds each (hover or focus
 * hold them), a dismiss button each. Form errors never go here: they stay inline with an
 * error summary. Mounted once by the console shell.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const t = useTranslations("ui.toast");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const show = useCallback((input: ToastInput) => {
    const id = nextId.current;
    nextId.current += 1;
    setToasts((current) =>
      [
        ...current,
        {
          id,
          title: input.title,
          tone: input.tone ?? "success",
          ...(input.body ? { body: input.body } : {}),
        },
      ].slice(-MAX_TOASTS),
    );
  }, []);

  const api = useMemo(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <section
        role="status"
        aria-live="polite"
        aria-label={t("region")}
        className="toast-region"
        data-print="hide"
      >
        {toasts.map((toast) => (
          <ToastCard key={toast.id} toast={toast} onDismiss={dismiss} />
        ))}
      </section>
    </ToastContext.Provider>
  );
}
