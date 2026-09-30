import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

// jsdom has no layout and logs "Not implemented" for window.scrollTo: a no-op keeps test
// output clean (scrolling itself is checked in e2e).
if (typeof window !== "undefined") {
  window.scrollTo = () => undefined;
}

/*
 * jsdom does not implement <dialog>. Emulate what the components rely on from the HTML
 * standard (and what Chrome and Edge do), so tests check the same behaviour users get:
 * - showModal() opens the dialog and runs the dialog focusing steps (the first element with
 *   `autofocus`, else the first focusable descendant);
 * - Escape on a page with an open modal dialog fires a cancelable `cancel` event on the
 *   topmost one and, unless cancelled, closes it (the close request);
 * - close() closes it, returns focus to the element focused before showModal() and fires
 *   `close` (synchronously here; browsers queue it, which tests do not depend on).
 * The page behind is not made inert; e2e covers that in a real browser.
 */
if (typeof HTMLDialogElement !== "undefined") {
  const proto = HTMLDialogElement.prototype;
  const emulate = (fn: unknown) =>
    typeof fn !== "function" || !String(fn).includes("[native code]");
  const modals: HTMLDialogElement[] = [];
  const returnFocus = new WeakMap<HTMLDialogElement, Element | null>();
  const FOCUSABLE =
    "button:not([disabled]), [href], input:not([disabled]):not([type=hidden]), select:not([disabled]), " +
    "textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])";

  if (emulate(proto.showModal)) {
    proto.showModal = function showModal(this: HTMLDialogElement) {
      if (this.hasAttribute("open")) return;
      returnFocus.set(this, document.activeElement);
      this.setAttribute("open", "");
      modals.push(this);
      const target =
        this.querySelector<HTMLElement>("[autofocus]") ??
        this.querySelector<HTMLElement>(FOCUSABLE);
      target?.focus();
    };
  }
  if (emulate(proto.close)) {
    proto.close = function close(this: HTMLDialogElement) {
      if (!this.hasAttribute("open")) return;
      this.removeAttribute("open");
      const index = modals.indexOf(this);
      if (index >= 0) modals.splice(index, 1);
      const previous = returnFocus.get(this);
      returnFocus.delete(this);
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
      this.dispatchEvent(new Event("close"));
    };
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const top = modals.findLast((dialog) => dialog.isConnected && dialog.hasAttribute("open"));
      if (!top) return;
      const cancel = new Event("cancel", { cancelable: true });
      if (top.dispatchEvent(cancel)) top.close();
    });
  }
}
