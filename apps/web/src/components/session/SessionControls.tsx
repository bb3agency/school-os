"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import {
  ACTIVITY_EVENT,
  defaultNavigate,
  keepAlive,
  loadSessionInfo,
  SESSION_INFO_EVENT,
  signOut,
  type Navigate,
  type SessionInfo,
  type SessionKind,
} from "@/lib/bff/session-client";

/** Warn this long before the idle timeout signs the user out. */
export const IDLE_WARNING_MS = 60_000;

/** Add reason=idle to our own signed-out page (the IdP's end-session URL is left alone). */
function idleTarget(target: string): string {
  if (!target.startsWith("/signed-out")) return target;
  return `${target}${target.includes("?") ? "&" : "?"}reason=idle`;
}

function signedOutIdle(kind: SessionKind): string {
  return idleTarget(kind === "staff" ? "/signed-out" : `/signed-out?kind=${kind}`);
}

export interface SessionControlsProps {
  kind: SessionKind;
  displayName?: string | null;
  tone?: "light" | "dark";
  /** Tests inject navigation; the app leaves the page. */
  navigate?: Navigate;
}

/**
 * Header controls for a signed-in session (docs/07 §5.2): who is signed in, a visible
 * "Lock now" button for shared office PCs, and the idle-timeout warning.
 */
export function SessionControls({
  kind,
  displayName,
  tone = "light",
  navigate = defaultNavigate,
}: SessionControlsProps) {
  const t = useTranslations("auth");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const hintId = useId();

  const lock = useCallback(async () => {
    setBusy(true);
    setFailed(false);
    try {
      await signOut(kind, navigate);
    } catch {
      setFailed(true);
      setBusy(false);
    }
  }, [kind, navigate]);

  return (
    <div className="flex flex-wrap items-center gap-3" data-print="hide">
      {displayName ? (
        <p className={cn("text-sm", tone === "dark" ? "text-platform-ink" : "text-ink-muted")}>
          {t("signedInAs", { name: displayName })}
        </p>
      ) : null}
      <Button
        variant="secondary"
        size="sm"
        onClick={() => void lock()}
        disabled={busy}
        aria-describedby={hintId}
      >
        {busy ? t("signingOut") : kind === "staff" ? t("lockNow") : t("signOut")}
      </Button>
      <span id={hintId} className="sr-only">
        {t("lockNowHint")}
      </span>
      {failed ? (
        <p
          role="alert"
          className={cn("text-sm", tone === "dark" ? "text-platform-ink" : "text-danger")}
        >
          {t("signOutFailed")}
        </p>
      ) : null}
      <IdleWarning kind={kind} navigate={navigate} />
    </div>
  );
}

/**
 * Native <dialog> shown 1 minute before the idle timeout. "Stay signed in" slides the
 * server-side timeout; doing nothing signs the user out (and ends the IdP session).
 * Every successful BFF call counts as activity (the server slides the timeout too).
 */
export function IdleWarning({ kind, navigate }: { kind: SessionKind; navigate: Navigate }) {
  const t = useTranslations("auth.idle");
  const dialogRef = useRef<HTMLDialogElement>(null);
  // The session's own idle timeout (the school's setting) arrives with the session info; the
  // server enforces it on every request, this only times the warning.
  const idleTimeoutRef = useRef(15 * 60_000);
  const absoluteRef = useRef(Number.POSITIVE_INFINITY);
  const [deadline, setDeadline] = useState<number | null>(null);
  const titleId = useId();
  const bodyId = useId();

  useEffect(() => {
    let cancelled = false;
    const apply = (info: SessionInfo) => {
      if (cancelled || !info.authenticated) return;
      idleTimeoutRef.current = info.idle_timeout_ms;
      const absolute = Date.parse(info.absolute_expires_at);
      if (Number.isFinite(absolute)) absoluteRef.current = absolute;
      setDeadline(Date.now() + info.expires_in_ms);
    };
    loadSessionInfo(kind)
      .then(apply)
      .catch(() => undefined);
    const onActivity = (event: Event) => {
      if ((event as CustomEvent<{ kind?: string }>).detail?.kind !== kind) return;
      setDeadline(Math.min(Date.now() + idleTimeoutRef.current, absoluteRef.current));
    };
    // Fresh facts (e.g. the school changed its idle timeout): time the new value (FR-TEN-012).
    const onInfo = (event: Event) => {
      const detail = (event as CustomEvent<{ kind?: string; info?: SessionInfo }>).detail;
      if (detail?.kind === kind && detail.info) apply(detail.info);
    };
    window.addEventListener(ACTIVITY_EVENT, onActivity);
    window.addEventListener(SESSION_INFO_EVENT, onInfo);
    return () => {
      cancelled = true;
      window.removeEventListener(ACTIVITY_EVENT, onActivity);
      window.removeEventListener(SESSION_INFO_EVENT, onInfo);
    };
  }, [kind]);

  const expire = useCallback(async () => {
    dialogRef.current?.close();
    try {
      await signOut(kind, (target) => navigate(idleTarget(target)));
    } catch {
      navigate(signedOutIdle(kind));
    }
  }, [kind, navigate]);

  const stay = useCallback(async () => {
    try {
      const info = await keepAlive(kind);
      if (!info.authenticated) {
        navigate(signedOutIdle(kind));
        return;
      }
      dialogRef.current?.close();
      setDeadline(Date.now() + info.expires_in_ms);
    } catch {
      await expire();
    }
  }, [kind, navigate, expire]);

  useEffect(() => {
    if (deadline === null) return;
    const dialog = dialogRef.current;
    const untilWarning = deadline - IDLE_WARNING_MS - Date.now();
    if (untilWarning > 0 && dialog?.open) dialog.close();
    const warn = setTimeout(
      () => {
        if (dialog && !dialog.open) dialog.showModal();
      },
      Math.max(0, untilWarning),
    );
    const end = setTimeout(() => void expire(), Math.max(0, deadline - Date.now()));
    return () => {
      clearTimeout(warn);
      clearTimeout(end);
    };
  }, [deadline, expire]);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onCancel={(event) => {
        // Escape means "I'm here".
        event.preventDefault();
        void stay();
      }}
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-lg border border-border bg-surface p-0 text-ink shadow-xl"
    >
      <div className="space-y-2 p-5">
        <h2 id={titleId} className="text-lg font-semibold">
          {t("title")}
        </h2>
        <p id={bodyId} className="text-sm text-ink-muted">
          {t("body")}
        </p>
      </div>
      <div className="flex flex-wrap justify-end gap-2 border-t border-border p-5">
        <Button variant="secondary" onClick={() => void expire()}>
          {t("signOut")}
        </Button>
        <Button onClick={() => void stay()}>{t("stay")}</Button>
      </div>
    </dialog>
  );
}
