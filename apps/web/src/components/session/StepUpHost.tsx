"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import {
  defaultNavigate,
  forgetSessionInfo,
  loadSessionInfo,
  type Navigate,
} from "@/lib/bff/session-client";
import {
  registerStepUpHandler,
  STEP_UP_CHANNEL,
  STEP_UP_COMPLETE,
  stepUpWindowUrl,
} from "@/lib/bff/step-up";

type Opener = (url: string) => Window | null;

const openWindow: Opener = (url) => window.open(url, "sos-step-up", "popup,width=520,height=720");

interface Pending {
  fallbackUrl: string;
  resolve: (confirmed: boolean) => void;
  /** Who was signed in when the prompt opened: someone else must not inherit the action. */
  who: { name: string | null; tenant: string | null } | null;
}

async function signedInAs(): Promise<Pending["who"]> {
  try {
    const info = await loadSessionInfo("staff", { fresh: true });
    return info.authenticated ? { name: info.display_name, tenant: info.active_tenant_id } : null;
  } catch {
    return null;
  }
}

/**
 * "Confirm it's you" for step-up actions (ADR-0018, docs/07 §5.2). Registered as the staff
 * step-up handler of the BFF client: when the API answers 428, this dialog offers to sign in
 * again in a small window. When that window reports completion (BroadcastChannel, same
 * origin, no data), the original request is sent once more and the form the user was filling
 * in stays as it was. If a different person signed in, nothing is retried and the page
 * reloads. When the window is blocked, "Continue in this tab" does the full-page step-up.
 */
export function StepUpHost({
  navigate = defaultNavigate,
  open: openStepUpWindow = openWindow,
}: {
  /** Tests inject navigation and the window opener. */
  navigate?: Navigate;
  open?: Opener;
}) {
  const t = useTranslations("errors.stepUp");
  const tc = useTranslations("common");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const pending = useRef<Pending | null>(null);
  const titleId = useId();
  const bodyId = useId();
  const [visible, setVisible] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [waiting, setWaiting] = useState(false);

  const finish = useCallback((confirmed: boolean) => {
    const current = pending.current;
    pending.current = null;
    setWaiting(false);
    setBlocked(false);
    setVisible(false);
    if (dialogRef.current?.open) dialogRef.current.close();
    returnFocus.current?.focus();
    current?.resolve(confirmed);
  }, []);

  const confirmed = useCallback(async () => {
    const current = pending.current;
    if (!current) return;
    forgetSessionInfo("staff");
    const now = await signedInAs();
    if (
      current.who &&
      now &&
      (now.name !== current.who.name || now.tenant !== current.who.tenant)
    ) {
      // Someone else signed in at the prompt: never replay this person's action for them.
      pending.current = null;
      current.resolve(false);
      navigate("/");
      return;
    }
    finish(true);
  }, [finish, navigate]);

  useEffect(() => {
    const unregister = registerStepUpHandler("staff", async (fallbackUrl) => {
      // One prompt at a time: a second 428 while one is open waits for the same answer.
      if (pending.current) {
        const first = pending.current;
        return new Promise<boolean>((resolve) => {
          const previous = first.resolve;
          first.resolve = (value) => {
            previous(value);
            resolve(value);
          };
        });
      }
      const who = await signedInAs();
      return new Promise<boolean>((resolve) => {
        pending.current = { fallbackUrl, resolve, who };
        returnFocus.current =
          document.activeElement instanceof HTMLElement ? document.activeElement : null;
        setVisible(true);
      });
    });
    return unregister;
  }, []);

  useEffect(() => {
    if (visible && dialogRef.current && !dialogRef.current.open) dialogRef.current.showModal();
  }, [visible]);

  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(STEP_UP_CHANNEL);
    channel.onmessage = (event: MessageEvent) => {
      if (event.data === STEP_UP_COMPLETE && pending.current) void confirmed();
    };
    return () => channel.close();
  }, [confirmed]);

  function signIn() {
    const opened = openStepUpWindow(stepUpWindowUrl());
    if (opened) {
      setBlocked(false);
      setWaiting(true);
    } else {
      setBlocked(true);
    }
  }

  function continueHere() {
    const current = pending.current;
    if (!current) return;
    navigate(current.fallbackUrl);
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onCancel={(event) => {
        event.preventDefault();
        finish(false);
      }}
      className="dialog-motion m-auto w-[min(32rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-0 text-ink shadow-popover"
    >
      {visible ? (
        <div className="space-y-4 p-5">
          <h2 id={titleId} className="text-lg font-semibold">
            {t("title")}
          </h2>
          <p id={bodyId} className="text-sm">
            {t("body")}
          </p>
          {waiting ? (
            <Alert tone="info" live>
              {t("waiting")}
            </Alert>
          ) : null}
          {blocked ? (
            <Alert tone="warning" live title={t("blockedTitle")}>
              {t("blockedBody")}
            </Alert>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
            <Button variant="secondary" onClick={() => finish(false)}>
              {tc("cancel")}
            </Button>
            {blocked ? (
              <Button variant="secondary" onClick={continueHere}>
                {t("continueHere")}
              </Button>
            ) : null}
            {waiting ? (
              <Button variant="secondary" onClick={() => void confirmed()}>
                {t("done")}
              </Button>
            ) : null}
            <Button onClick={signIn}>{waiting ? t("openAgain") : t("signIn")}</Button>
          </div>
        </div>
      ) : null}
    </dialog>
  );
}
