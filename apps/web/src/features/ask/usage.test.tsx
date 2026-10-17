import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../../messages/en.json";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { me } from "@/test/records-fixtures";
import { intlErrors, renderWithIntl } from "@/test/render";
import {
  INITIAL_ASK,
  isSafeAppPath,
  needsResolve,
  noticeOfReason,
  searchOnlyNotice,
  stateFromMessage,
} from "./answer";
import { renderChat } from "./chat-test-utils";
import type { ConversationMessage } from "./data";
import { ResolveSourceLink } from "./ResolveSource";
import { bannerKey, bannerOf, showsNumbers, usedPercent, type Usage } from "./usage";
import { AskUsageBanner, AskUsageMeter } from "./UsageNotices";

// Ask finish-up (R2.4): usage meter, limit banners, search-only reasons, source links.
// FR-KB-040, FR-KB-041, FR-KB-042 (US-2501..US-2503). Synthetic data only.

const nav = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/ask",
    useRouter: () => ({ push: nav.push, replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const U = en.ask.usage;
const K = en.ask.kbErrors;
const A = en.ask.answer;
const USAGE_ROUTE = "GET /bff/api/v1/knowledge/usage";
const RESOLVE_ROUTE = "GET /bff/api/v1/knowledge/resolve";
const DOC = "0192f3a4-0000-7000-8000-00000000c701";

function usage(overrides: Partial<Usage> = {}): Usage {
  return {
    month: "2026-10",
    state: "ok",
    search_only_reason: null,
    answers_used: 312,
    included_answers: 1000,
    ...overrides,
  };
}

const STAFF = { answers_used: null, included_answers: null } as const;

let stub: BffStub;

beforeEach(() => {
  nav.push.mockReset();
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["kb.ask", "tenant.billing.read"]));
});

afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("usage helpers (FR-KB-040, FR-KB-041)", () => {
  it("shows numbers only when the API sent them", () => {
    expect(showsNumbers(usage())).toBe(true);
    expect(showsNumbers(usage(STAFF))).toBe(false);
    expect(showsNumbers(undefined)).toBe(false);
  });

  it("computes the share of the included answers, capped at 100", () => {
    expect(usedPercent(usage())).toBe(31);
    expect(usedPercent(usage({ answers_used: 1500 }))).toBe(100);
    expect(usedPercent(usage({ included_answers: null }))).toBeNull();
  });

  it("banners: search-only for everyone, near-limit only for billing readers", () => {
    expect(bannerOf(usage())).toBeNull();
    expect(bannerOf(usage({ state: "near_limit" }))).toEqual({ kind: "near_limit" });
    expect(bannerOf(usage({ state: "near_limit", ...STAFF }))).toBeNull();
    expect(
      bannerOf(
        usage({ state: "search_only", search_only_reason: "ai_budget_exhausted", ...STAFF }),
      ),
    ).toEqual({ kind: "search_only", reason: "ai_budget_exhausted" });
    expect(bannerOf(usage({ state: "search_only" }))).toEqual({
      kind: "search_only",
      reason: "ai_unavailable",
    });
    const near = usage({ state: "near_limit" });
    expect(bannerKey(near, { kind: "near_limit" })).toBe("2026-10:near_limit:");
  });

  it("maps every API reason to its explanation; none means the answer was not checked", () => {
    expect(noticeOfReason("ai_budget_exhausted")).toBe("budget");
    expect(noticeOfReason("ai_disabled")).toBe("disabled");
    expect(noticeOfReason("ai_rate_limited")).toBe("rate_limited");
    expect(noticeOfReason("ai_unavailable")).toBe("unavailable");
    expect(noticeOfReason("answer_unchecked")).toBe("unchecked");
    expect(noticeOfReason(null)).toBeNull();
    expect(searchOnlyNotice({ notice: null })).toBe("unchecked");
    expect(searchOnlyNotice({ notice: "budget" })).toBe("budget");
    for (const key of ["budget", "disabled", "rate_limited", "unavailable", "unchecked"]) {
      expect(K[key as keyof typeof K]).toBeTruthy();
    }
  });

  it("a stored search-only answer carries its reason after a reload", () => {
    const message: ConversationMessage = {
      query_id: "0192f3a4-0000-7000-8000-00000000e001",
      question: "When do exams begin?",
      answer: null,
      answer_withheld: false,
      status: "search_only",
      mode: "search_only",
      language: "en",
      citations: [],
      feedback: null,
      followups: [],
      created_at: "2026-10-01T05:00:00Z",
      superseded: false,
      cached: false,
      summarized: false,
      search_only_reason: "ai_disabled",
    };
    expect(stateFromMessage(message).notice).toBe("disabled");
    expect(stateFromMessage({ ...message, search_only_reason: null }).notice).toBeNull();
    expect(INITIAL_ASK.notice).toBeNull();
  });

  it("asks the API only for sos:// sources this build cannot open, and opens only app paths", () => {
    expect(needsResolve(`sos://doc/${DOC}/v1#p1`)).toBe(false);
    expect(needsResolve(`sos://timetable/${DOC}`)).toBe(true);
    expect(needsResolve("https://evil.example/x")).toBe(false);
    expect(needsResolve("sos://")).toBe(false);
    expect(isSafeAppPath(`/documents/${DOC}`)).toBe(true);
    expect(isSafeAppPath("/ask/c/x#m-y")).toBe(true);
    expect(isSafeAppPath("//evil.example")).toBe(false);
    expect(isSafeAppPath("https://evil.example")).toBe(false);
    expect(isSafeAppPath("/\\evil")).toBe(false);
  });
});

describe("usage meter (US-2501)", () => {
  it("shows 'used of included' with a meter to billing readers", () => {
    renderWithIntl(<AskUsageMeter usage={usage()} />);
    expect(screen.getByText("312 of 1,000 answers this month")).toBeInTheDocument();
    const meter = screen.getByRole("meter", { name: U.meterLabel });
    expect(meter).toHaveAttribute("value", "312");
    expect(meter).toHaveAttribute("max", "1000");
    expect(screen.queryByText(U.near)).not.toBeInTheDocument();
  });

  it("says near the limit in words from 80%", () => {
    renderWithIntl(<AskUsageMeter usage={usage({ state: "near_limit", answers_used: 850 })} />);
    expect(screen.getByText(U.near)).toBeInTheDocument();
  });

  it("without a bundle: the count only, no meter", () => {
    renderWithIntl(<AskUsageMeter usage={usage({ included_answers: null })} />);
    expect(screen.getByText("312 answers this month")).toBeInTheDocument();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });

  it("other staff see no numbers at all", () => {
    const { container } = renderWithIntl(<AskUsageMeter usage={usage(STAFF)} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("limit banners (US-2501 AC2, US-2502)", () => {
  it("search-only because the budget is used up: reason and whom to ask, dismissible", async () => {
    renderWithIntl(
      <AskUsageBanner
        usage={usage({ state: "search_only", search_only_reason: "ai_budget_exhausted", ...STAFF })}
      />,
    );
    const banner = screen.getByTestId("ask-usage-banner");
    expect(within(banner).getByText(U.searchOnlyTitle)).toBeInTheDocument();
    expect(within(banner).getByText(K.budget)).toBeInTheDocument();
    expect(within(banner).getByText(U.next.ai_budget_exhausted)).toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: `Dismiss: ${U.searchOnlyTitle}` }));
    expect(screen.queryByTestId("ask-usage-banner")).not.toBeInTheDocument();
  });

  it("billing readers are told to ask support about a bigger bundle", () => {
    renderWithIntl(
      <AskUsageBanner
        usage={usage({ state: "search_only", search_only_reason: "ai_budget_exhausted" })}
      />,
    );
    expect(screen.getByText(U.nextBudgetBilling)).toBeInTheDocument();
  });

  it("AI switched off: says so and what to do", () => {
    renderWithIntl(
      <AskUsageBanner
        usage={usage({ state: "search_only", search_only_reason: "ai_disabled", ...STAFF })}
      />,
    );
    expect(screen.getByText(K.disabled)).toBeInTheDocument();
    expect(screen.getByText(U.next.ai_disabled)).toBeInTheDocument();
  });

  it("near the limit: a quiet notice for billing readers, nothing for other staff", () => {
    const { unmount } = renderWithIntl(<AskUsageBanner usage={usage({ state: "near_limit" })} />);
    expect(screen.getByText(U.nearTitle)).toBeInTheDocument();
    unmount();
    const { container } = renderWithIntl(
      <AskUsageBanner usage={usage({ state: "near_limit", ...STAFF })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("the Ask page shows the meter and the banner (FR-KB-041)", () => {
  it("meter in the header for billing readers and the search-only banner above the composer", async () => {
    stub.routes[USAGE_ROUTE] = () =>
      Response.json(
        usage({
          state: "search_only",
          search_only_reason: "ai_budget_exhausted",
          answers_used: 1000,
        }),
      );
    renderChat();
    expect(await screen.findByText("1,000 of 1,000 answers this month")).toBeInTheDocument();
    expect(await screen.findByText(U.searchOnlyTitle)).toBeInTheDocument();
    expect(screen.getByText(U.nextBudgetBilling)).toBeInTheDocument();
  });

  it("other staff: no numbers, and no banner while answers are not limited", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["kb.ask"]));
    let calls = 0;
    stub.routes[USAGE_ROUTE] = () => {
      calls += 1;
      return Response.json(usage({ state: "near_limit", ...STAFF }));
    };
    renderChat();
    await waitFor(() => expect(calls).toBeGreaterThan(0));
    expect(screen.queryByTestId("ask-usage-meter")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ask-usage-banner")).not.toBeInTheDocument();
  });
});

describe("source links through /knowledge/resolve (US-2503)", () => {
  const SOURCE = `sos://timetable/${DOC}`;

  it("opens the screen the API names", async () => {
    let asked = "";
    stub.routes[RESOLVE_ROUTE] = (_request, url) => {
      asked = url.searchParams.get("source") ?? "";
      return Response.json({ kind: "doc", id: DOC, href: `/documents/${DOC}` });
    };
    renderWithIntl(<ResolveSourceLink source={SOURCE} name="Timetable" />);
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: `Timetable (${A.resolveOpen})` }));
    await waitFor(() => expect(nav.push).toHaveBeenCalled());
    expect(asked).toBe(SOURCE);
    expect(String(nav.push.mock.calls[0]?.[0])).toContain(`/documents/${DOC}`);
  });

  it("a source the member can no longer open reads so (404)", async () => {
    stub.routes[RESOLVE_ROUTE] = () => problem(404, "not_found");
    renderWithIntl(<ResolveSourceLink source={SOURCE} name="Timetable" />);
    await userEvent.setup().click(screen.getByRole("button", { name: /Timetable/ }));
    expect(await screen.findByText(A.resolveGone)).toBeInTheDocument();
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("never navigates to anything but an app path", async () => {
    stub.routes[RESOLVE_ROUTE] = () =>
      Response.json({ kind: "doc", id: DOC, href: "//evil.example/x" });
    renderWithIntl(<ResolveSourceLink source={SOURCE} name="Timetable" />);
    await userEvent.setup().click(screen.getByRole("button", { name: /Timetable/ }));
    expect(await screen.findByText(A.resolveNoScreen)).toBeInTheDocument();
    expect(nav.push).not.toHaveBeenCalled();
  });
});
