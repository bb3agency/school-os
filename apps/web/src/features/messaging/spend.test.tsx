import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WhatsAppSettingsPage from "@/app/[locale]/(school)/settings/whatsapp/page";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { CONFIGURE, READ, type Account, type Settings } from "./data";
import { InrBanner, istToday, type Spend } from "./SpendCard";

/**
 * FP-11 (FR-MSG-031..034): the "Messages and estimated Meta cost" card and the banner for an
 * account not billed in rupees. Synthetic values only; no recipient data exists in the card.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/settings/whatsapp",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.messaging;

function connected(overrides: Partial<Account> = {}): Account {
  return {
    connected: true,
    available: true,
    manual_token_allowed: false,
    embedded_signup: null,
    inr_required_from: "2027-01-01",
    unreviewed_texts: [],
    id: "0192f3a4-0000-7000-8000-00000000f101",
    mode: "manual_token",
    status: "active",
    display_phone: "+91 90000 01234",
    display_name: "Synthetic Model School",
    currency: "INR",
    currency_state: "confirmed_inr",
    templates: [],
    version: 3,
    ...overrides,
  };
}

const SETTINGS: Settings = {
  notices_enabled: false,
  absence_alerts_enabled: false,
  fee_reminders_enabled: false,
  fee_receipts_enabled: false,
  absence_alert_time: "11:00:00",
  quiet_start: "21:00:00",
  quiet_end: "07:00:00",
  daily_cap: null,
  default_daily_cap: 225,
  office_phone: null,
  no_message_dates: [],
  utility_rate_inr: "0.115",
  gst_percent: "18",
  version: 0,
};

const SPEND: Spend = {
  months: [
    {
      month: "2026-11",
      sent: 1240,
      billable: 1200,
      pending: 30,
      estimated_cost_inr: "166.98",
      lines: [
        {
          pricing_category: "utility",
          rate: "utility_out_of_window",
          purpose: "school_notice",
          sent: 1200,
          billable: 1170,
          free: 0,
          pending: 30,
          estimated_cost_inr: "162.84",
        },
        {
          pricing_category: "utility",
          rate: "utility_in_window",
          purpose: "optin_confirm",
          sent: 30,
          billable: 30,
          free: 0,
          pending: 0,
          estimated_cost_inr: "4.07",
        },
        {
          pricing_category: "service",
          rate: "service_above_free_tier",
          purpose: "auto_reply",
          sent: 10,
          billable: 0,
          free: 10,
          pending: 0,
          estimated_cost_inr: "0.00",
        },
      ],
    },
    {
      month: "2026-10",
      sent: 0,
      billable: 0,
      pending: 0,
      estimated_cost_inr: "0.00",
      lines: [],
    },
  ],
  gst_percent: "18",
  rates: [
    { rate: "utility_out_of_window", inr: "0.115", verified: false, used: true },
    { rate: "marketing", inr: "0.8631", verified: false, used: false },
  ],
  rates_checked_on: "2026-11-09",
  next_rate_change: "2027-01-01",
  rates_recheck_due: false,
  service_free_per_month: 1000,
  auto_reply_days: 90,
};

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/messaging/settings"] = () => Response.json(SETTINGS);
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions, { roles: ["owner"] }));
}

describe("messages and estimated Meta cost (FR-MSG-032, FR-MSG-033)", () => {
  it("shows each month and the chosen month's purposes with rupees only", async () => {
    signedIn([CONFIGURE, READ]);
    stub.routes["GET /bff/api/v1/messaging/account"] = () => Response.json(connected());
    stub.routes["GET /bff/api/v1/messaging/spend"] = () => Response.json(SPEND);
    renderWithIntl(<WhatsAppSettingsPage />);
    expect(await screen.findByRole("heading", { name: en.spend.title })).toBeInTheDocument();
    const november = await screen.findByRole("row", { name: /November 2026/ });
    expect(within(november).getByText("₹166.98")).toBeInTheDocument();
    expect(within(november).getByText("1,240")).toBeInTheDocument();
    const call = stub.callsTo("GET /bff/api/v1/messaging/spend")[0];
    expect(call?.url.searchParams.get("months")).toBe("6");
    // November's breakdown by purpose, then October (nothing sent).
    expect(screen.getByText(en.log.purposes.school_notice)).toBeInTheDocument();
    expect(screen.getByText(en.spend.autoReply)).toBeInTheDocument();
    expect(screen.getByText(`${en.spend.categories.utility} (${en.spend.inWindow})`)).toBeVisible();
    await userEvent.selectOptions(screen.getByLabelText(en.spend.monthPicker), "2026-10");
    expect(screen.getByText(en.spend.noneInMonth)).toBeInTheDocument();
    expect(screen.queryByText(en.spend.recheckTitle)).toBeNull();
    expect(screen.getByText(/not yet against Meta's rupee rate card/)).toBeInTheDocument();
  });

  it("says when Meta may have changed its rates", async () => {
    signedIn([READ]);
    stub.routes["GET /bff/api/v1/messaging/account"] = () => Response.json(connected());
    stub.routes["GET /bff/api/v1/messaging/spend"] = () =>
      Response.json({ ...SPEND, rates_recheck_due: true });
    renderWithIntl(<WhatsAppSettingsPage />);
    expect(await screen.findByText(en.spend.recheckTitle)).toBeInTheDocument();
    expect(screen.getByText(/01\/01\/2027/)).toBeInTheDocument();
  });

  it("is hidden for a section-scoped reader (403) and without message.read", async () => {
    signedIn([READ]);
    stub.routes["GET /bff/api/v1/messaging/account"] = () => Response.json(connected());
    stub.routes["GET /bff/api/v1/messaging/spend"] = () => problem(403, "forbidden");
    const first = renderWithIntl(<WhatsAppSettingsPage />);
    expect(await screen.findByText("+91 90000 01234")).toBeInTheDocument();
    await waitFor(() => expect(stub.callsTo("GET /bff/api/v1/messaging/spend")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("heading", { name: en.spend.title })).toBeNull());
    first.unmount();
    signedIn([CONFIGURE]);
    renderWithIntl(<WhatsAppSettingsPage />);
    expect(await screen.findByText("+91 90000 01234")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: en.spend.title })).toBeNull();
    expect(stub.callsTo("GET /bff/api/v1/messaging/spend")).toHaveLength(1);
  });

  it("types the API answer strictly enough to refuse a wrong shape", () => {
    // The generated MessagingSpendOut type: the fixture fits it and a wrong shape does not.
    const ok: Spend = SPEND;
    expect(Array.isArray(ok.months)).toBe(true);
    // @ts-expect-error months is a list of months, not a string.
    const wrong: Spend = { ...SPEND, months: "none" };
    expect(Array.isArray(wrong.months)).toBe(false);
  });
});

describe("billing currency banner (FR-MSG-034)", () => {
  it("before 1 Jan 2027 lists the steps and the last day", () => {
    renderWithIntl(
      <InrBanner
        account={connected({ currency: "USD", currency_state: "not_inr" })}
        today="2026-11-09"
      />,
    );
    expect(
      screen.getByText(en.account.inrTitle.replace("{date}", "31/12/2026")),
    ).toBeInTheDocument();
    for (const step of Object.values(en.account.inrSteps)) {
      expect(screen.getByText(step)).toBeInTheDocument();
    }
  });

  it("from the cutoff says sending has stopped", () => {
    renderWithIntl(
      <InrBanner
        account={connected({ currency: "USD", currency_state: "not_inr" })}
        today="2027-01-01"
      />,
    );
    expect(screen.getByText(en.account.inrStoppedTitle)).toBeInTheDocument();
  });

  it("is not shown for an account in rupees or not known yet", () => {
    const { container } = renderWithIntl(
      <>
        <InrBanner account={connected()} today="2026-11-09" />
        <InrBanner account={connected({ currency_state: "unknown" })} today="2026-11-09" />
      </>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("works out today's date in India", () => {
    expect(istToday(new Date("2026-12-31T19:00:00Z"))).toBe("2027-01-01");
    expect(istToday(new Date("2026-12-31T18:00:00Z"))).toBe("2026-12-31");
  });
});
