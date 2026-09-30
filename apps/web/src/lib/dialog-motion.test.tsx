import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { closeDialog, CLOSING_ATTRIBUTE, useDialogClose } from "./dialog-motion";

/**
 * Dialog exit motion (docs/17 §5.5, NFR-A11Y-001): the exit plays with the Web Animations
 * API (no style attribute, SEC-010) and then the native close runs; without the API or
 * under reduced motion the dialog closes at once.
 */

function openDialog(): HTMLDialogElement {
  const dialog = document.createElement("dialog");
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

/** A stand-in for element.animate whose animation finishes when `finish()` is called. */
function stubAnimate() {
  let finish: () => void = () => undefined;
  const finished = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const animate = vi.fn(() => ({ finished }) as unknown as Animation);
  Object.defineProperty(HTMLElement.prototype, "animate", {
    configurable: true,
    writable: true,
    value: animate,
  });
  return { animate, finish: () => finish() };
}

afterEach(() => {
  // jsdom has no Web Animations API: remove the stand-in again.
  delete (HTMLElement.prototype as { animate?: unknown }).animate;
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("closeDialog (docs/17 §5.5)", () => {
  it("closes at once where the Web Animations API is missing", () => {
    const dialog = openDialog();
    closeDialog(dialog, "modal");
    expect(dialog.open).toBe(false);
    expect(dialog).not.toHaveAttribute(CLOSING_ATTRIBUTE);
  });

  it("plays a 150ms exit, then closes natively (the close event still fires)", async () => {
    const { animate, finish } = stubAnimate();
    const dialog = openDialog();
    const onClose = vi.fn();
    dialog.addEventListener("close", onClose);

    closeDialog(dialog, "modal");
    expect(dialog.open).toBe(true);
    expect(dialog).toHaveAttribute(CLOSING_ATTRIBUTE);
    expect(animate).toHaveBeenCalledWith(
      [
        { opacity: 1, transform: "none" },
        { opacity: 0, transform: "scale(0.97)" },
      ],
      { duration: 150, easing: "cubic-bezier(0.23, 1, 0.32, 1)" },
    );
    // A second close while the exit plays does not start another one.
    closeDialog(dialog, "modal");
    expect(animate).toHaveBeenCalledTimes(1);

    await act(async () => {
      finish();
      await Promise.resolve();
    });
    expect(dialog.open).toBe(false);
    expect(dialog).not.toHaveAttribute(CLOSING_ATTRIBUTE);
    await vi.waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("the drawer leaves the way it came in (to the left)", () => {
    const { animate } = stubAnimate();
    closeDialog(openDialog(), "drawer");
    expect(animate).toHaveBeenCalledWith(
      [{ transform: "translateX(0)" }, { transform: "translateX(-100%)" }],
      expect.objectContaining({ duration: 150 }),
    );
  });

  it("closes at once under prefers-reduced-motion", () => {
    const { animate } = stubAnimate();
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({ matches: query === "(prefers-reduced-motion: reduce)" })),
    );
    const dialog = openDialog();
    closeDialog(dialog, "modal");
    expect(animate).not.toHaveBeenCalled();
    expect(dialog.open).toBe(false);
  });

  it("does nothing for a dialog that is not open", () => {
    const { animate } = stubAnimate();
    const dialog = document.createElement("dialog");
    closeDialog(dialog, "modal");
    closeDialog(null, "modal");
    expect(animate).not.toHaveBeenCalled();
  });
});

function Harness() {
  const ref = useRef<HTMLDialogElement>(null);
  const close = useDialogClose(ref, "modal");
  return (
    <>
      <button type="button" onClick={() => ref.current?.showModal()}>
        Open
      </button>
      <dialog ref={ref} aria-label="Sample">
        <button type="button" onClick={close}>
          Done
        </button>
      </dialog>
    </>
  );
}

describe("useDialogClose", () => {
  it("routes Escape through the exit when the cancel event can be cancelled", async () => {
    const { animate, finish } = stubAnimate();
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    const dialog = screen.getByRole("dialog", { name: "Sample" }) as HTMLDialogElement;

    const cancel = new Event("cancel", { cancelable: true });
    act(() => {
      dialog.dispatchEvent(cancel);
    });
    expect(cancel.defaultPrevented).toBe(true);
    expect(animate).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish();
      await Promise.resolve();
    });
    expect(dialog.open).toBe(false);
  });

  it("without the Web Animations API, Escape and the button close natively at once", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    const dialog = screen.getByRole("dialog", { name: "Sample" }) as HTMLDialogElement;
    const cancel = new Event("cancel", { cancelable: true });
    dialog.dispatchEvent(cancel);
    expect(cancel.defaultPrevented).toBe(false);
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(dialog.open).toBe(false);
  });
});
