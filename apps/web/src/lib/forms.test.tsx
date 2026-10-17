import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { ApiError } from "@/lib/bff/query";
import { renderWithIntl } from "@/test/render";
import { useApiForm } from "./forms";

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
