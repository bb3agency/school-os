"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, type RefObject } from "react";

/**
 * Controlled open for the form dialogs (ActionDialog, FormDialog): a parent that passes
 * `open` opens and closes the native `<dialog>` itself, for example from a "More" menu item,
 * without a hidden trigger button. `undefined` keeps the dialog uncontrolled (its own trigger).
 *
 * Only a change of `open` acts, so a dialog the person closed (Escape, Cancel, success) stays
 * closed until the parent, told through `onOpenChange(false)`, opens it again. Focus returns to
 * the element that had it when the dialog opened (the menu's trigger once the menu has closed,
 * or the dialog's own trigger).
 */
export function useControlledOpen({
  open,
  dialogRef,
  show,
  close,
}: {
  open: boolean | undefined;
  dialogRef: RefObject<HTMLDialogElement | null>;
  show: () => void;
  close: () => void;
}): void {
  const actions = useRef({ show, close });
  useLayoutEffect(() => {
    actions.current = { show, close };
  });
  useEffect(() => {
    if (open === undefined) return;
    const dialog = dialogRef.current;
    if (open && !dialog?.open) actions.current.show();
    else if (!open && dialog?.open) actions.current.close();
  }, [open, dialogRef]);
}

/** Remembers the focused element when a dialog opens, to return focus there when it closes. */
export function useReturnFocus(): {
  remember: () => void;
  restore: (fallback: HTMLElement | null) => void;
} {
  const previous = useRef<HTMLElement | null>(null);
  return useMemo(
    () => ({
      remember: () => {
        const active = document.activeElement;
        previous.current =
          active instanceof HTMLElement && active !== document.body ? active : null;
      },
      restore: (fallback) => {
        const target =
          fallback && fallback.isConnected && !fallback.hidden ? fallback : previous.current;
        previous.current = null;
        if (target?.isConnected) target.focus();
      },
    }),
    [],
  );
}
