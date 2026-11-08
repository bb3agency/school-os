import { act, waitFor } from "@testing-library/react";
import { expect, vi } from "vitest";
import { PASSIVE_HEADER } from "@/lib/bff/fetch";
import { problem, type BffStub, type StubCall } from "./bff-stub";

/**
 * Status poll checks shared by the feature tests (XB-07 passive polls, XO-08 transient
 * failures). Run them under `vi.useFakeTimers({ shouldAdvanceTime: true })`, so findBy and
 * waitFor still work while `advance` jumps over the poll intervals.
 */

export async function advance(ms: number, times = 1): Promise<void> {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }
}

/** The first read is the person's own (active); every automatic poll after it is passive. */
export function expectPassivePolls(calls: readonly StubCall[]): void {
  const [first, ...polls] = calls;
  expect(first?.headers.get(PASSIVE_HEADER)).toBeNull();
  expect(polls.length).toBeGreaterThan(0);
  for (const poll of polls) expect(poll.headers.get(PASSIVE_HEADER)).toBe("1");
}

/**
 * Answers `busy` on `route`, except reads 2 and 3 fail with `status` (two in a row, so a
 * query that retries once still ends in its error state). With `lasting` every read from the
 * second on fails. Returns a function that counts the reads so far.
 */
export function failSecondRead(
  stub: BffStub,
  route: string,
  busy: () => Response,
  status: number,
  lasting = false,
): () => number {
  let reads = 0;
  stub.routes[route] = () => {
    reads += 1;
    if (reads >= 2 && (lasting || reads <= 3)) {
      return problem(status, status === 429 ? "rate_limited" : "forbidden");
    }
    return busy();
  };
  return () => stub.callsTo(route).length;
}

/** Polls through a transient failure: more reads after it, all of them passive. */
export async function expectPollsThroughTransient(
  stub: BffStub,
  route: string,
  stepMs: number,
  steps = 8,
): Promise<void> {
  await waitFor(() => expect(stub.callsTo(route).length).toBeGreaterThanOrEqual(1));
  await advance(stepMs, steps);
  expect(stub.callsTo(route).length).toBeGreaterThanOrEqual(5);
  expectPassivePolls(stub.callsTo(route));
}

/** Stops asking after a failure that asking again does not fix (403, 404 …). */
export async function expectPollStops(
  stub: BffStub,
  route: string,
  stepMs: number,
  steps = 8,
): Promise<void> {
  await waitFor(() => expect(stub.callsTo(route).length).toBeGreaterThanOrEqual(1));
  await advance(stepMs, steps);
  expect(stub.callsTo(route).length).toBeGreaterThanOrEqual(2);
  const settled = stub.callsTo(route).length;
  await advance(stepMs, steps);
  expect(stub.callsTo(route)).toHaveLength(settled);
}
