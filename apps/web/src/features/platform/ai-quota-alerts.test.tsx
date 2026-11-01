import { screen } from "@testing-library/react";
import type { AiQuotaAlert } from "@schoolos/api-client";
import { describe, expect, it } from "vitest";
import { loading, ready } from "@/lib/loadable";
import { messages, renderWithIntl } from "@/test/render";
import { AiQuotaAlerts } from "./AiQuotaAlerts";

/**
 * Operator usage view: schools at 80 % or 100 % of their AI answer bundle's included answers
 * this month (ADR-0038 follow-up, docs/16 §19 Q16; FR-PLT-020). Counts only. Synthetic data only.
 */
const q = messages.en.platform.usage.quota;
const A = "0192f3a4-0000-7000-8000-00000000000a";
const B = "0192f3a4-0000-7000-8000-00000000000b";
const names: Record<string, string> = { [A]: "Synthetic School A", [B]: "Synthetic School B" };
const nameOf = (id: string) => names[id] ?? id;
const row = (tenant_id: string, threshold: 80 | 100, used: number): AiQuotaAlert => ({
  tenant_id,
  threshold,
  month_start: "2026-11-01",
  answers_used: used,
  included_answers: 1000,
  crossed_at: "2026-11-02T20:00:00Z",
});

describe("AiQuotaAlerts", () => {
  it("lists each school's highest alert, all-used first", () => {
    renderWithIntl(
      <AiQuotaAlerts
        alerts={ready<readonly AiQuotaAlert[]>([row(A, 80, 812), row(B, 100, 1040)])}
        schoolName={nameOf}
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: q.title })).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Synthetic School B");
    expect(items[0]).toHaveTextContent(q.usedUp);
    expect(items[0]).toHaveTextContent("1,040 of 1,000");
    expect(items[1]).toHaveTextContent("Synthetic School A");
    expect(items[1]).toHaveTextContent(q.near);
  });

  it("none this month: says so; nothing while loading", () => {
    const { unmount } = renderWithIntl(
      <AiQuotaAlerts alerts={ready<readonly AiQuotaAlert[]>([])} schoolName={nameOf} />,
    );
    expect(screen.getByText(q.none)).toBeInTheDocument();
    unmount();
    renderWithIntl(<AiQuotaAlerts alerts={loading} schoolName={nameOf} />);
    expect(screen.queryByRole("heading", { name: q.title })).toBeNull();
  });
});
