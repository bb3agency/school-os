import { act, fireEvent, waitFor } from "@testing-library/react";
import { expect } from "vitest";
import type { BffStub } from "./bff-stub";

/**
 * Two submits (or clicks) in the same frame, as a double Enter or double click on a slow PC
 * produces: React has not re-rendered the disabled button between them (WD-06, XP-02, XP-05).
 */
export function fireTwice(target: Element, kind: "submit" | "click"): void {
  act(() => {
    fireEvent[kind](target);
    fireEvent[kind](target);
  });
}

/** Exactly one request reached `key` ("PUT /bff/api/v1/..."), also a moment later. */
export async function expectOneCall(stub: BffStub, key: string): Promise<void> {
  await waitFor(() => expect(stub.callsTo(key)).toHaveLength(1));
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(stub.callsTo(key)).toHaveLength(1);
}
