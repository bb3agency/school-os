import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { STEP_UP_CHANNEL, STEP_UP_COMPLETE } from "@/lib/bff/step-up";
import { renderWithIntl } from "@/test/render";
import { StepUpCompleteView } from "./StepUpCompleteView";

describe("step-up completion window (ADR-0018)", () => {
  it("tells the opening page that sign-in is done, with no data, and tries to close", async () => {
    const close = vi.spyOn(window, "close").mockImplementation(() => {});
    const received: unknown[] = [];
    const channel = new BroadcastChannel(STEP_UP_CHANNEL);
    channel.onmessage = (event: MessageEvent) => received.push(event.data);
    renderWithIntl(<StepUpCompleteView />, "te");
    expect(screen.getByRole("heading", { name: "మీరు మళ్లీ సైన్ ఇన్ చేశారు" })).toBeInTheDocument();
    await vi.waitFor(() => expect(received).toEqual([STEP_UP_COMPLETE]));
    expect(close).toHaveBeenCalled();
    channel.close();
  });
});
