"use client";

import { useCallback, useEffect, type RefObject } from "react";
import { cssEasing, DURATION, EASE_OUT, prefersReducedMotion } from "./motion";

/**
 * Exit motion for native `<dialog>`s (docs/17 §5.5). The entrance is CSS (`.dialog-motion`,
 * `.drawer` in globals.css); the exit needs script because a closed dialog is gone at once.
 * `closeDialog()` plays a short exit with the Web Animations API (no style attribute, so the
 * CSP allows it; SEC-010) and then calls `dialog.close()`, so the `close` event, focus return
 * and the modal semantics stay exactly those of the native element. Without the Web
 * Animations API, or under prefers-reduced-motion, it closes at once.
 *
 * Exits are faster than entrances (150ms, `EASE_OUT`). Escape goes through the same exit when
 * the browser lets the `cancel` event be cancelled; otherwise it closes natively at once.
 */

export type DialogMotionKind = "modal" | "drawer";

const EXIT_FRAMES: Record<DialogMotionKind, Keyframe[]> = {
  // Modals stay centred: fade and scale back to 0.97 (the entrance in reverse).
  modal: [
    { opacity: 1, transform: "none" },
    { opacity: 0, transform: "scale(0.97)" },
  ],
  // The drawer leaves the way it came in.
  drawer: [{ transform: "translateX(0)" }, { transform: "translateX(-100%)" }],
};

/** Attribute set while the exit plays (the backdrop fades out with it, globals.css). */
export const CLOSING_ATTRIBUTE = "data-closing";

function canAnimate(dialog: HTMLDialogElement): boolean {
  return typeof dialog.animate === "function" && !prefersReducedMotion();
}

/** Close `dialog` after its exit motion (or at once when motion is off or unsupported). */
export function closeDialog(dialog: HTMLDialogElement | null, kind: DialogMotionKind): void {
  if (!dialog?.open || dialog.hasAttribute(CLOSING_ATTRIBUTE)) return;
  if (!canAnimate(dialog)) {
    dialog.close();
    return;
  }
  dialog.setAttribute(CLOSING_ATTRIBUTE, "");
  const finish = () => {
    dialog.removeAttribute(CLOSING_ATTRIBUTE);
    if (dialog.open) dialog.close();
  };
  try {
    const animation = dialog.animate(EXIT_FRAMES[kind], {
      duration: DURATION.quick,
      easing: cssEasing(EASE_OUT),
    });
    animation.finished.then(finish, finish);
  } catch {
    finish();
  }
}

/**
 * Wires the exit motion to a dialog: returns a `close()` that plays it, and routes Escape
 * (the dialog's `cancel` event) through it too.
 */
export function useDialogClose(
  ref: RefObject<HTMLDialogElement | null>,
  kind: DialogMotionKind,
): () => void {
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const onCancel = (event: Event) => {
      if (!event.cancelable || !canAnimate(dialog)) return;
      event.preventDefault();
      // Some browsers refuse to cancel a repeated Escape: then it closes natively.
      if (event.defaultPrevented) closeDialog(dialog, kind);
    };
    dialog.addEventListener("cancel", onCancel);
    return () => dialog.removeEventListener("cancel", onCancel);
  }, [ref, kind]);
  return useCallback(() => closeDialog(ref.current, kind), [ref, kind]);
}
