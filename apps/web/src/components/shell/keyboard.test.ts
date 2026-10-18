import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  nextBaseline,
  opensKeyboard,
  useOnScreenKeyboard,
  viewportSuggestsKeyboard,
} from "./keyboard";

describe("on-screen keyboard detection (docs/17 §5.7)", () => {
  it("keeps the tallest height seen at one width and resets on rotation", () => {
    let baseline = nextBaseline(null, { width: 360, height: 640 });
    baseline = nextBaseline(baseline, { width: 360, height: 380 });
    expect(baseline).toEqual({ width: 360, height: 640 });
    baseline = nextBaseline(baseline, { width: 360, height: 700 });
    expect(baseline.height).toBe(700);
    expect(nextBaseline(baseline, { width: 640, height: 360 })).toEqual({
      width: 640,
      height: 360,
    });
  });

  it("reads a much shorter viewport at the same width as a keyboard", () => {
    const baseline = { width: 360, height: 640 };
    expect(viewportSuggestsKeyboard(baseline, { width: 360, height: 360 })).toBe(true);
    // The address bar collapsing or expanding is not a keyboard.
    expect(viewportSuggestsKeyboard(baseline, { width: 360, height: 584 })).toBe(false);
    // A rotation is not a keyboard either.
    expect(viewportSuggestsKeyboard(baseline, { width: 640, height: 300 })).toBe(false);
  });

  it("knows which fields open a keyboard", () => {
    const text = document.createElement("input");
    const tel = document.createElement("input");
    tel.type = "tel";
    const box = document.createElement("input");
    box.type = "checkbox";
    const readOnly = document.createElement("input");
    readOnly.readOnly = true;
    const area = document.createElement("textarea");
    const button = document.createElement("button");
    expect(opensKeyboard(text)).toBe(true);
    expect(opensKeyboard(tel)).toBe(true);
    expect(opensKeyboard(area)).toBe(true);
    expect(opensKeyboard(box)).toBe(false);
    expect(opensKeyboard(readOnly)).toBe(false);
    expect(opensKeyboard(button)).toBe(false);
    expect(opensKeyboard(null)).toBe(false);
  });

  describe("useOnScreenKeyboard", () => {
    const original = window.matchMedia;
    afterEach(() => {
      window.matchMedia = original;
      document.body.innerHTML = "";
    });

    function touch(coarse: boolean) {
      window.matchMedia = vi.fn().mockImplementation((query: string) => ({
        matches: coarse && query === "(pointer: coarse)",
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })) as unknown as typeof window.matchMedia;
    }

    it("is open while a text field has focus on a touch screen", () => {
      touch(true);
      const input = document.createElement("input");
      document.body.append(input);
      const { result } = renderHook(() => useOnScreenKeyboard());
      expect(result.current).toBe(false);
      act(() => input.focus());
      expect(result.current).toBe(true);
      vi.useFakeTimers();
      act(() => {
        input.blur();
        vi.runAllTimers();
      });
      vi.useRealTimers();
      expect(result.current).toBe(false);
    });

    it("ignores focus on a computer with a mouse", () => {
      touch(false);
      const input = document.createElement("input");
      document.body.append(input);
      const { result } = renderHook(() => useOnScreenKeyboard());
      act(() => input.focus());
      expect(result.current).toBe(false);
    });
  });
});
