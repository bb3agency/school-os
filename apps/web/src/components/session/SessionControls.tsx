"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
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
  /**
   * "header" (default): one row for the top bars outside the consoles, with the idle
   * warning. "sidebar": the account area at the foot of the console sidebar (docs/17 §5.2);
   * the console mounts the idle warning once itself (AppShell `session`).
   */
  variant?: "header" | "sidebar";
  /** Sidebar variant: the person's role(s), already translated (optional second line). */
  role?: string | null;
}

/**
 * Controls for a signed-in session (docs/07 §5.2): who is signed in, a visible "Lock now"
 * button for shared office PCs, and (header variant) the idle-timeout warning.
 */
export function SessionControls({
  kind,
  displayName,
  tone = "light",
  navigate = defaultNavigate,
  variant = "header",
  role = null,
}: SessionControlsProps) {
  const t = useTranslations("auth");
  const ts = useTranslations("shell");
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

  const lockLabel = busy ? t("signingOut") : kind === "staff" ? t("lockNow") : t("signOut");
  const dark = tone === "dark";

  if (variant === "sidebar") {
    return (
      <section aria-label={ts("account")} className="space-y-2.5" data-print="hide">
        {displayName ? (
          <div className="flex min-w-0 items-center gap-2.5 px-1 collapsed:justify-center collapsed:px-0">
            <Avatar name={displayName} decorative />
            <div className="min-w-0 flex-1 collapsed:sr-only">
              <p
                className={cn(
                  "text-sm font-semibold break-anywhere",
                  dark ? "text-platform-ink" : "text-ink",
                )}
              >
                {/* Heard as "Signed in as …"; seen as the name under "Your account". */}
                <span className="sr-only">{t("signedInAs", { name: displayName })}</span>
                <span aria-hidden="true">{displayName}</span>
              </p>
              {role ? (
                <p className={cn("text-xs", dark ? "text-platform-muted" : "text-ink-subtle")}>
                  {role}
                </p>
              ) : null}
            </div>
          </div>
        ) : null}
        <button
          type="button"
          onClick={() => void lock()}
          disabled={busy}
          aria-describedby={hintId}
          data-tooltip={lockLabel}
          className={cn(
            "flex min-h-10 w-full items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition-colors disabled:opacity-60",
            "collapsed:px-0",
            dark
              ? "border-platform-hover text-platform-ink hover:bg-platform-hover"
              : "border-border-soft bg-surface text-ink hover:bg-surface-muted",
          )}
        >
          <Icon name="lock" className="size-4.5" />
          <span className="collapsed:sr-only">{lockLabel}</span>
        </button>
        <span id={hintId} className="sr-only">
          {t("lockNowHint")}
        </span>
        {failed ? (
          <p
            role="alert"
            className={cn("text-sm break-anywhere", dark ? "text-platform-ink" : "text-danger")}
          >
            {t("signOutFailed")}
          </p>
        ) : null}
      </section>
    );
  }

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
        {lockLabel}
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
 * Mount exactly one per page: the header SessionControls includes it; the consoles mount it
 * through AppShell's `session` slot.
 */
export function IdleWarning({
  kind,
  navigate = defaultNavigate,
}: {
  kind: SessionKind;
  navigate?: Navigate;
}) {
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
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-0 text-ink shadow-popover"
    >
      <div className="space-y-2 p-5">
        <h2 id={titleId} className="text-lg font-semibold">
          {t("title")}
        </h2>
        <p id={bodyId} className="text-sm text-ink-muted">
          {t("body")}
        </p>
      </div>
      <div className="flex flex-wrap justify-end gap-2 rounded-b-xl border-t border-border bg-surface-muted px-5 py-4">
        <Button variant="secondary" onClick={() => void expire()}>
          {t("signOut")}
        </Button>
        <Button onClick={() => void stay()}>{t("stay")}</Button>
      </div>
    </dialog>
  );
}
