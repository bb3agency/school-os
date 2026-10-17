import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PaymentsSettingsPage from "@/app/[locale]/(school)/settings/payments/page";
import OnlineReconciliationPage from "@/app/[locale]/(school)/fees/reconcile/online/page";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import type { Account } from "@/features/fees/data";
import {
  CONFIGURE,
  FINANCE_READ,
  KEY_ID,
  MANAGE,
  COLLECT,
  isOpen,
  type Connection,
  type Intent,
  type ReconItem,
} from "./data";
import { PaymentLinks } from "./PaymentLinks";

/**
 * Online payments screens (R2-2D; ADR-0042; US-2107, US-2108, US-2111): connect the school's own
 * Razorpay account (the webhook secret shown once), payment links on a fee account, and online
 * reconciliation. Synthetic values only; Razorpay never gets personal data.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/settings/payments",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.payments;
const te = messages.te.payments;
const CONNECTION = "0192f3a4-0000-7000-8000-00000000a001";
const ACCOUNT = "0192f3a4-0000-7000-8000-00000000a101";
const INTENT = "0192f3a4-0000-7000-8000-00000000a201";
const ITEM = "0192f3a4-0000-7000-8000-00000000a301";

function connection(overrides: Partial<Connection> = {}): Connection {
  return {
    connected: false,
    provider: "razorpay",
    manual_keys_allowed: true,
    oauth_available: false,
    webhook_events: [],
    open_links: 0,
    pending_events: 0,
    failed_events: 0,
    ...overrides,
  };
}

function connected(overrides: Partial<Connection> = {}): Connection {
  return connection({
    connected: true,
    id: CONNECTION,
    mode: "manual_keys",
    environment: "test",
    status: "active",
    key_id_hint: "rzp_test_…ABCD",
    webhook_url: "https://app.example.test/api/v1/webhooks/razorpay/ROUTEROUTEROUTEROUTE1234",
    webhook_events: ["payment_link.paid"],
    connected_at: "2026-10-18T05:00:00Z",
    version: 1,
    ...overrides,
  });
}

function intent(overrides: Partial<Intent> = {}): Intent {
  return {
    id: INTENT,
    account_id: ACCOUNT,
    intent_code: "ABCD1234",
    amount: "3500.00",
    instalments: [1],
    status: "created",
    short_url: "https://rzp.example.test/abc",
    expires_at: "2099-01-01T00:00:00Z",
    created_via: "staff",
    created_by: null,
    created_by_name: "Synthetic Clerk",
    created_at: "2026-10-18T05:00:00Z",
    paid_at: null,
    receipt_id: null,
    receipt_number: null,
    cancelled_at: null,
    reused: false,
    version: 1,
    ...overrides,
  };
}

function feeAccount(): Account {
  return {
    id: ACCOUNT,
    student_id: "0192f3a4-0000-7000-8000-00000000a401",
    display_name: "Synthetica Student",
    admission_no: "F/1",
    class_section: "IX-A",
    academic_year_id: "0192f3a4-0000-7000-8000-00000000a501",
    plan_id: "0192f3a4-0000-7000-8000-00000000a601",
    plan_name: "Class IX",
    category_code: null,
    status: "open",
    lines: [
      {
        instalment_no: 1,
        label: "Term 1",
        due_on: "2026-09-01",
        head_id: null,
        head_code: "TUI",
        head_name: "Tuition",
        collectable: true,
        charged: "3000.00",
        concession: "0.00",
        late_fee: "0.00",
        adjustment: "0.00",
        paid: "0.00",
        due: "3000.00",
        overdue: true,
      },
      {
        instalment_no: 1,
        label: "Term 1",
        due_on: "2026-09-01",
        head_id: null,
        head_code: "BUS",
        head_name: "Transport",
        collectable: false,
        charged: "1000.00",
        concession: "0.00",
        late_fee: "0.00",
        adjustment: "0.00",
        paid: "0.00",
        due: "1000.00",
        overdue: true,
      },
    ],
    totals: {
      charged: "4000.00",
      concessions: "0.00",
      late_fees: "0.00",
      adjustments: "0.00",
      paid: "0.00",
      refunded: "0.00",
      balance: "4000.00",
      overdue: "3000.00",
      collectable: "3000.00",
    },
    pending_requests: 0,
    version: 1,
  } as Account;
}

function item(overrides: Partial<ReconItem> = {}): ReconItem {
  return {
    id: ITEM,
    kind: "paid_not_receipted",
    reason_code: "dues_already_paid",
    status: "open",
    provider_payment_id: "pay_SYNTHABC",
    provider_refund_id: null,
    intent_id: INTENT,
    intent_code: "ABCD1234",
    receipt_id: null,
    receipt_number: null,
    refund_id: null,
    account_id: ACCOUNT,
    amount: "3500.00",
    expected_amount: "3500.00",
    note: null,
    explained_by: null,
    explained_by_name: null,
    explained_at: null,
    resolved_at: null,
    created_at: "2026-10-18T00:30:00Z",
    run_date: "2026-10-17",
    version: 1,
    ...overrides,
  };
}

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions, { roles: ["owner"] }));
}

describe("helpers", () => {
  it("accepts Razorpay key ids only", () => {
    expect(KEY_ID.test("rzp_live_ABCDEF123")).toBe(true);
    expect(KEY_ID.test("rzp_test_abcdef")).toBe(true);
    expect(KEY_ID.test("rzp_prod_ABCDEF")).toBe(false);
    expect(KEY_ID.test("rzp_live_AB")).toBe(false);
  });

  it("knows open links", () => {
    expect(isOpen({ status: "created", expires_at: "2099-01-01T00:00:00Z" })).toBe(true);
    expect(isOpen({ status: "created", expires_at: "2000-01-01T00:00:00Z" })).toBe(false);
    expect(isOpen({ status: "paid", expires_at: "2099-01-01T00:00:00Z" })).toBe(false);
  });

  it("has every payments message in Telugu too", () => {
    const keys = (value: unknown, prefix = ""): string[] =>
      value && typeof value === "object"
        ? Object.entries(value).flatMap(([k, v]) => keys(v, `${prefix}${k}.`))
        : [prefix];
    expect(keys(te).sort()).toEqual(keys(en).sort());
  });
});

describe("Online payments settings (US-2107)", () => {
  it("says fees are off (404 module_hidden)", async () => {
    signedIn([CONFIGURE]);
    stub.routes["GET /bff/api/v1/payments/connection"] = () => problem(404, "module_hidden");
    renderWithIntl(<PaymentsSettingsPage />);
    expect(await screen.findByText(en.off.title)).toBeInTheDocument();
  });

  it("explains the school is the merchant and connects with keys; the secret is shown once", async () => {
    signedIn([CONFIGURE, FINANCE_READ]);
    stub.routes["GET /bff/api/v1/payments/connection"] = () => Response.json(connection());
    stub.routes["POST /bff/api/v1/payments/connection/manual"] = () =>
      Response.json(
        {
          connection: connected(),
          webhook: {
            url: "https://app.example.test/api/v1/webhooks/razorpay/ROUTE",
            secret: "synthetic-webhook-secret-shown-once",
            events: ["payment_link.paid", "refund.processed"],
            previous_valid_until: null,
          },
        },
        { status: 201 },
      );
    renderWithIntl(<PaymentsSettingsPage />);
    expect(await screen.findByText(en.connect.ownAccount)).toBeInTheDocument();
    expect(screen.getByText(en.connect.neverHeld)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: en.connect.manual }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(messages.en.common.stepUpNote)).toBeInTheDocument();
    await userEvent.type(
      within(dialog).getByLabelText(new RegExp(en.connect.keyId)),
      "rzp_test_SYNTHABCDEF",
    );
    await userEvent.type(
      within(dialog).getByLabelText(new RegExp(en.connect.keySecret)),
      "synthetic-key-secret",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: en.connect.connect }));
    expect(await within(dialog).findByText("payment_link.paid")).toBeInTheDocument();
    expect(within(dialog).getByDisplayValue("synthetic-webhook-secret-shown-once")).toBeVisible();
    const call = stub.callsTo("POST /bff/api/v1/payments/connection/manual")[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      key_id: "rzp_test_SYNTHABCDEF",
      key_secret: "synthetic-key-secret",
    });
    expect(call?.url.search).toBe("");
  });

  it("refuses a malformed key id before sending", async () => {
    signedIn([CONFIGURE]);
    stub.routes["GET /bff/api/v1/payments/connection"] = () => Response.json(connection());
    renderWithIntl(<PaymentsSettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: en.connect.manual }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(new RegExp(en.connect.keyId)), "abc");
    await userEvent.type(
      within(dialog).getByLabelText(new RegExp(en.connect.keySecret)),
      "synthetic-key-secret",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: en.connect.connect }));
    expect(
      await within(dialog).findByText(messages.en.validation.invalidKeyId),
    ).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/payments/connection/manual")).toHaveLength(0);
  });

  it("shows a connected account to a reader without the actions", async () => {
    signedIn([FINANCE_READ]);
    stub.routes["GET /bff/api/v1/payments/connection"] = () => Response.json(connected());
    renderWithIntl(<PaymentsSettingsPage />);
    expect(await screen.findByText(en.account.statuses.active)).toBeInTheDocument();
    expect(screen.getByText(/webhooks\/razorpay/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.account.disconnect })).toBeNull();
  });

  it("asks only the owner to connect", async () => {
    signedIn([FINANCE_READ]);
    stub.routes["GET /bff/api/v1/payments/connection"] = () => Response.json(connection());
    renderWithIntl(<PaymentsSettingsPage />);
    expect(await screen.findByText(en.connect.askOwner)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.connect.manual })).toBeNull();
  });
});

describe("Payment links (US-2108)", () => {
  it("lists links and makes one for chosen instalments", async () => {
    signedIn([COLLECT]);
    stub.routes["GET /bff/api/v1/payments/connection"] = () => Response.json(connected());
    stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}/payment-intents`] = () =>
      Response.json([
        intent({ status: "paid", receipt_id: ITEM, receipt_number: "RCT/2026-27/000001" }),
      ]);
    stub.routes[`POST /bff/api/v1/fees/accounts/${ACCOUNT}/payment-intents`] = () =>
      Response.json(intent(), { status: 201 });
    renderWithIntl(<PaymentLinks account={feeAccount()} />);
    expect(await screen.findByText("RCT/2026-27/000001")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: en.links.create }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: en.links.create }));
    expect(await within(dialog).findByDisplayValue("https://rzp.example.test/abc")).toBeVisible();
    const call = stub.callsTo(`POST /bff/api/v1/fees/accounts/${ACCOUNT}/payment-intents`)[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ instalments: [1] });
    expect(call?.headers.get("Idempotency-Key")).toBeTruthy();
  });

  it("says when online payments are not connected", async () => {
    signedIn([COLLECT]);
    stub.routes["GET /bff/api/v1/payments/connection"] = () => Response.json(connection());
    stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}/payment-intents`] = () =>
      Response.json([]);
    renderWithIntl(<PaymentLinks account={feeAccount()} />);
    expect(await screen.findByText(en.links.notConnectedTitle)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.links.create })).toBeNull();
  });
});

describe("Online reconciliation (US-2111)", () => {
  it("lists exceptions and explains one with a note", async () => {
    signedIn([MANAGE, FINANCE_READ]);
    stub.routes["GET /bff/api/v1/fees/reconciliation/online"] = () =>
      Response.json({ items: [item()], next_cursor: null, open_count: 1 });
    stub.routes["GET /bff/api/v1/fees/reconciliation/online/summary"] = () =>
      Response.json({
        connected: true,
        open_count: 1,
        pending_events: 0,
        failed_events: 0,
        runs: [
          {
            run_date: "2026-10-17",
            status: "done",
            payments_seen: 3,
            exceptions_opened: 1,
            error_code: null,
            finished_at: "2026-10-18T00:31:00Z",
          },
        ],
      });
    stub.routes[`POST /bff/api/v1/fees/reconciliation/online/items/${ITEM}/explain`] = () =>
      Response.json(item({ status: "explained", note: "Synthetic: refunded in dashboard" }));
    renderWithIntl(<OnlineReconciliationPage />);
    expect(await screen.findByText(en.recon.reasons.dues_already_paid)).toBeInTheDocument();
    expect(screen.getByText(en.recon.reasons.dues_already_paid)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: en.recon.explain }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByLabelText(new RegExp(en.recon.note)),
      "Synthetic: refunded in dashboard",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: en.recon.explain }));
    await waitFor(() =>
      expect(
        stub.callsTo(`POST /bff/api/v1/fees/reconciliation/online/items/${ITEM}/explain`),
      ).toHaveLength(1),
    );
    const call = stub.callsTo(
      `POST /bff/api/v1/fees/reconciliation/online/items/${ITEM}/explain`,
    )[0];
    expect(call?.headers.get("If-Match")).toBe('W/"1"');
  });

  it("offers no explain button to readers", async () => {
    signedIn([FINANCE_READ]);
    stub.routes["GET /bff/api/v1/fees/reconciliation/online"] = () =>
      Response.json({ items: [item()], next_cursor: null, open_count: 1 });
    stub.routes["GET /bff/api/v1/fees/reconciliation/online/summary"] = () =>
      Response.json({
        connected: true,
        open_count: 1,
        pending_events: 0,
        failed_events: 0,
        runs: [],
      });
    renderWithIntl(<OnlineReconciliationPage />);
    expect(await screen.findByText(en.recon.reasons.dues_already_paid)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.recon.explain })).toBeNull();
  });
});
