import { screen } from "@testing-library/react";
import type { TenantAiAnswers } from "@schoolos/api-client";
import { describe, expect, it } from "vitest";
import { loading, ready } from "@/lib/loadable";
import { messages, renderWithIntl } from "@/test/render";
import { AiAnswersMeter } from "./AiAnswersMeter";

/**
 * Operator usage tab: a school's AI answers this month against its bundle (docs/18 R4,
 * FR-PLT-020, ADR-0038). Numbers straight from the API; counts only. Synthetic data only.
 */
const T = "0192f3a4-0000-7000-8000-000000000001";
const um = messages.en.platform.usage.ai;
const bundle = {
  code: "ai-standard",
  name: "Standard",
  included_answers: 1000,
  price_inr: "1499.00",
  overage_rate_inr: "1.50",
  counts_from: "2026-10-01",
  month_start: "2026-11-01",
  answers_used: 1250,
  answers_counted_to: "2026-11-20",
  extra_answers: 250,
  overage_so_far_inr: "375.00",
};

describe("AiAnswersMeter", () => {
  it("shows answers so far against the quota, extra answers and the overage before GST", () => {
    renderWithIntl(<AiAnswersMeter answers={ready<TenantAiAnswers>({ tenant_id: T, bundle })} />);
    expect(screen.getByRole("heading", { level: 3, name: um.title })).toBeInTheDocument();
    expect(screen.getByText("Standard bundle")).toBeInTheDocument();
    const meter = screen.getByRole("meter", { name: um.used });
    expect(meter).toHaveAttribute("max", "1000");
    expect(screen.getByText("1,250 of 1,000", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(um.extra).nextElementSibling).toHaveTextContent("250");
    expect(screen.getByText(um.overage).nextElementSibling).toHaveTextContent("₹375.00");
    expect(screen.getByText(/^Counted up to .* at ₹1\.50 each\.$/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/token/i);
  });

  it("shows the month's quota alert (ADR-0038 follow-up, docs/16 §19 Q16)", () => {
    const { unmount } = renderWithIntl(
      <AiAnswersMeter
        answers={ready<TenantAiAnswers>({ tenant_id: T, bundle: { ...bundle, quota_alert: 100 } })}
      />,
    );
    expect(
      screen.getByText("All included answers used; extra answers are billed at ₹1.50 each"),
    ).toBeInTheDocument();
    expect(screen.getByText(um.quotaNote)).toBeInTheDocument();
    unmount();
    const near = renderWithIntl(
      <AiAnswersMeter
        answers={ready<TenantAiAnswers>({
          tenant_id: T,
          bundle: { ...bundle, answers_used: 800, extra_answers: 0, quota_alert: 80 },
        })}
      />,
    );
    expect(screen.getByText(um.quotaNear)).toBeInTheDocument();
    near.unmount();
    renderWithIntl(<AiAnswersMeter answers={ready<TenantAiAnswers>({ tenant_id: T, bundle })} />);
    expect(screen.queryByText(um.quotaNote)).toBeNull();
  });

  it("before the bundle counts: no meter, says when counting starts", () => {
    renderWithIntl(
      <AiAnswersMeter
        answers={ready<TenantAiAnswers>({
          tenant_id: T,
          bundle: {
            ...bundle,
            counts_from: "2026-12-01",
            answers_used: null,
            answers_counted_to: null,
            extra_answers: null,
            overage_so_far_inr: null,
          },
        })}
      />,
    );
    expect(screen.queryByRole("meter")).toBeNull();
    expect(screen.getByText(/^Answers count against this bundle from/)).toBeInTheDocument();
  });

  it("no bundle: says so; nothing while loading", () => {
    const { unmount } = renderWithIntl(
      <AiAnswersMeter answers={ready<TenantAiAnswers>({ tenant_id: T, bundle: null })} />,
    );
    expect(screen.getByText(um.none)).toBeInTheDocument();
    unmount();
    renderWithIntl(<AiAnswersMeter answers={loading} />);
    expect(screen.queryByRole("heading", { name: um.title })).toBeNull();
  });
});
