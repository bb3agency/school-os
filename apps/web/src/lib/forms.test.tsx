import { act, fireEvent, renderHook, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as z from "@/lib/zod";
import { ApiError } from "@/lib/bff/query";
import { renderWithIntl } from "@/test/render";
import { useApiForm, useIntentKey } from "./forms";

/** Let the submit promise and its finally() run. */
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

const schema = z.object({ name: z.string().min(1) });

function NameForm({
  submit,
}: {
  submit: (data: { name: string }, idempotencyKey: string) => Promise<unknown>;
}) {
  const form = useApiForm({ schema, submit });
  return (
    <form onSubmit={form.onSubmit} aria-label="Name form">
      <label htmlFor="name">Name</label>
      <input id="name" name="name" defaultValue="Ravi" />
      <button type="submit">Save</button>
    </form>
  );
}

describe("useApiForm Idempotency-Key and double submit (docs/09 §2)", () => {
  it("a second submit while the first is running is ignored", async () => {
    let finish: (value: unknown) => void = () => {};
    const submit = vi.fn(() => new Promise((resolve) => (finish = resolve)));
    renderWithIntl(<NameForm submit={submit} />);
    const form = screen.getByRole("form", { name: "Name form" });
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(submit).toHaveBeenCalledTimes(1);
    finish({});
    await settle();
    fireEvent.submit(form);
    expect(submit).toHaveBeenCalledTimes(2);
  });

  it("retrying the same values after a server error reuses the key", async () => {
    const submit = vi
      .fn<(data: { name: string }, key: string) => Promise<unknown>>()
      .mockRejectedValueOnce(new ApiError(503, "service_unavailable"))
      .mockResolvedValueOnce({});
    renderWithIntl(<NameForm submit={submit} />);
    const form = screen.getByRole("form", { name: "Name form" });
    fireEvent.submit(form);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    await settle();
    fireEvent.submit(form);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
    expect(submit.mock.calls[1]?.[1]).toBe(submit.mock.calls[0]?.[1]);
  });

  it("changed values after a server error get a new key (no idempotency_key_reused)", async () => {
    const submit = vi
      .fn<(data: { name: string }, key: string) => Promise<unknown>>()
      .mockRejectedValueOnce(new ApiError(503, "service_unavailable"))
      .mockResolvedValueOnce({});
    renderWithIntl(<NameForm submit={submit} />);
    const form = screen.getByRole("form", { name: "Name form" });
    fireEvent.submit(form);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    await settle();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ravi Kumar" } });
    fireEvent.submit(form);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
    expect(submit.mock.calls[1]?.[0]).toEqual({ name: "Ravi Kumar" });
    expect(submit.mock.calls[1]?.[1]).not.toBe(submit.mock.calls[0]?.[1]);
  });
});

describe("useIntentKey (WD-02, docs/09 §2)", () => {
  it("keeps the key for a retry of the same values; success, a 4xx or a change start afresh", () => {
    const { result } = renderHook(() => useIntentKey());
    const intent = result.current;
    const first = intent.next({ title: "Fee report" });
    intent.failed(new ApiError(502, "bad_gateway"));
    expect(intent.next({ title: "Fee report" })).toBe(first);
    intent.failed(new TypeError("Failed to fetch"));
    expect(intent.next({ title: "Fee report" })).toBe(first);
    const changed = intent.next({ title: "Fee report for October" });
    expect(changed).not.toBe(first);
    intent.failed(new ApiError(422, "validation_error"));
    const afterRefusal = intent.next({ title: "Fee report for October" });
    expect(afterRefusal).not.toBe(changed);
    intent.succeeded();
    expect(intent.next({ title: "Fee report for October" })).not.toBe(afterRefusal);
  });
});
