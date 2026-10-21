import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DuesScreen } from "@/features/fees/ReportsScreens";
import type { Dues } from "@/features/fees/data";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import {
  READ,
  SEND,
  type FeeReminderPreview,
  type Message,
  type MessagePage,
  type Settings,
} from "./data";
import { MessageHistory } from "./MessagesScreen";

/**
 * R2-4 close-out screens: "Send payment link" from the dues list, one row or many (FR-MSG-027;
 * US-2108, US-2110 AC3), and "Stop link" on a sent message (ADR-0046 decision 5, FR-PUB-007),
 * also shown on the fee account's Messages tab (FR-MSG-028). Synthetic values only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/fees/dues",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en;
const ACCOUNT_A = "0192f3a4-0000-7000-8000-00000000f001";
const STUDENT_A = "0192f3a4-0000-7000-8000-00000000e001";
const MESSAGE = "0192f3a4-0000-7000-8000-00000000f301";

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/messaging/settings"] = () => Response.json(settings());
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["principal"] }));
}

function settings(): Settings {
  return {
    notices_enabled: true,
    absence_alerts_enabled: true,
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
    version: 1,
  };
}

function dues(): Dues {
  return {
    as_of: "2026-10-21",
    students: 1,
    total_overdue: "3500.00",
    rows: [
      {
        account_id: ACCOUNT_A,
        student_id: STUDENT_A,
        display_name: "Synthetica Dues A",
        admission_no: "F-0001",
        class_section: "Class IX A",
        overdue: "3500.00",
        oldest_due_on: "2026-09-21",
        balance: "7000.00",
        last_payment_on: null,
      },
    ],
  };
}

function preview(overrides: Partial<FeeReminderPreview> = {}): FeeReminderPreview {
  return {
    preview_token: "fr_" + "b".repeat(40),
    accounts: 1,
    accounts_reminded: 1,
    guardians: 1,
    recipients: 1,
    skipped_accounts: {},
    skipped_guardians: {},
    estimated_cost_inr: "0.14",
    utility_rate_inr: "0.115",
    gst_percent: "18",
    blocked_reason: null,
    ...overrides,
  };
}

function message(overrides: Partial<Message> = {}): Message {
  return {
    id: MESSAGE,
    purpose: "fee_reminder",
    kind: "template",
    template_key: "fee_reminder",
    template_version: 2,
    language: "en",
    language_fallback: false,
    guardian_id: "0192f3a4-0000-7000-8000-00000000d001",
    student_id: STUDENT_A,
    source_type: "fee_account",
    source_id: ACCOUNT_A,
    phone_last4: "4321",
    status: "delivered",
    skip_reason: null,
    error_code: null,
    error_reason: null,
    attempts: 1,
    has_link: true,
    queued_at: "2026-10-21T05:30:00Z",
    sent_at: "2026-10-21T05:31:00Z",
    delivered_at: "2026-10-21T05:32:00Z",
    read_at: null,
    failed_at: null,
    ...overrides,
  };
}

describe("Send payment link from the dues list (FR-MSG-027)", () => {
  beforeEach(() => {
    stub.routes["GET /bff/api/v1/fees/dues"] = () => Response.json(dues());
    stub.routes["GET /bff/api/v1/classes"] = () => Response.json({ data: [], next_cursor: null });
  });

  it("sends one row's payment link from its actions menu with the preview token", async () => {
    signedIn(["finance.read", SEND, READ]);
    stub.routes["POST /bff/api/v1/fees/reminders/preview"] = () => Response.json(preview());
    stub.routes["POST /bff/api/v1/fees/reminders"] = () =>
      Response.json(
        { accounts: 1, recipients: 1, queued: 1, skipped_accounts: {}, skipped_guardians: {} },
        { status: 202 },
      );
    renderWithIntl(<DuesScreen />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Actions for Synthetica Dues A" }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", { name: en.fees.dues.sendPaymentLink }),
    );
    const dialog = await screen.findByRole("dialog", { name: en.messaging.paymentLinks.title });
    const confirm = await within(dialog).findByRole("button", { name: "Send 1 payment link" });
    expect(confirm).toHaveAccessibleDescription(en.messaging.paymentLinks.consequence);
    expect(
      JSON.parse(stub.callsTo("POST /bff/api/v1/fees/reminders/preview")[0]?.body ?? "{}"),
    ).toEqual({ account_ids: [ACCOUNT_A], all_matching: false, kind: "payment_link" });
    await userEvent.click(confirm);
    expect(
      await within(dialog).findByText("1 payment link is queued. It goes out outside quiet hours."),
    ).toBeInTheDocument();
    const sent = stub.callsTo("POST /bff/api/v1/fees/reminders");
    expect(sent).toHaveLength(1);
    expect(sent[0]?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(sent[0]?.body ?? "{}")).toMatchObject({
      kind: "payment_link",
      preview_token: preview().preview_token,
    });
  });

  it("says online payments must be connected and keeps the send button disabled", async () => {
    signedIn(["finance.read", SEND, READ]);
    stub.routes["POST /bff/api/v1/fees/reminders/preview"] = () =>
      Response.json(preview({ blocked_reason: "payments_not_connected" }));
    renderWithIntl(<DuesScreen />);
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Select Synthetica Dues A" }),
    );
    const bar = screen.getByRole("region", { name: en.ui.table.bulkActions });
    await userEvent.click(within(bar).getByRole("button", { name: en.fees.dues.sendPaymentLink }));
    const dialog = await screen.findByRole("dialog", { name: en.messaging.paymentLinks.title });
    expect(
      await within(dialog).findByText(en.messaging.feeReminders.paymentsNotConnected),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("link", { name: en.messaging.feeReminders.openPayments }),
    ).toHaveAttribute("href", "/settings/payments");
    expect(within(dialog).getByRole("button", { name: "Send 1 payment link" })).toBeDisabled();
  });

  it("offers no row actions without message.send", async () => {
    signedIn(["finance.read"]);
    renderWithIntl(<DuesScreen />);
    expect(await screen.findByText("Synthetica Dues A")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Actions for Synthetica Dues A" })).toBeNull();
  });
});

describe("Stop link on a sent message (ADR-0046 decision 5)", () => {
  it("confirms with the consequence line and posts to revoke-link", async () => {
    signedIn([READ, SEND]);
    stub.routes["POST /bff/api/v1/messages/search"] = () =>
      Response.json({ items: [message()], next_cursor: null } satisfies MessagePage);
    stub.routes[`POST /bff/api/v1/messages/${MESSAGE}/revoke-link`] = () =>
      Response.json({ message_id: MESSAGE, revoked: true });
    renderWithIntl(<MessageHistory studentId={STUDENT_A} tableId="fee-account-messages" />);
    const stop = await screen.findByRole("button", { name: /Stop the link of the Fee reminder/ });
    const search = stub.callsTo("POST /bff/api/v1/messages/search")[0];
    expect(JSON.parse(search?.body ?? "{}")).toMatchObject({ student_id: STUDENT_A });
    await userEvent.click(stop);
    const dialog = await screen.findByRole("dialog", { name: en.messaging.revokeLink.title });
    const confirm = within(dialog).getByRole("button", { name: en.messaging.revokeLink.confirm });
    expect(confirm).toHaveAccessibleDescription(en.messaging.revokeLink.consequence);
    await userEvent.click(confirm);
    expect(await within(dialog).findByText(en.messaging.revokeLink.revoked)).toBeInTheDocument();
    expect(stub.callsTo(`POST /bff/api/v1/messages/${MESSAGE}/revoke-link`)).toHaveLength(1);
  });

  it("shows no stop button without message.send or for a message without a live link", async () => {
    signedIn([READ]);
    stub.routes["POST /bff/api/v1/messages/search"] = () =>
      Response.json({ items: [message()], next_cursor: null } satisfies MessagePage);
    renderWithIntl(<MessageHistory studentId={STUDENT_A} />);
    expect(await screen.findByText(en.messaging.log.statuses.delivered)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Stop the link/ })).toBeNull();
  });

  it("offers no stop for a failed message", async () => {
    signedIn([READ, SEND]);
    stub.routes["POST /bff/api/v1/messages/search"] = () =>
      Response.json({
        items: [message({ status: "failed", error_reason: "not_on_whatsapp" })],
        next_cursor: null,
      } satisfies MessagePage);
    renderWithIntl(<MessageHistory />);
    await waitFor(() =>
      expect(screen.getByText(en.messaging.log.reasons.not_on_whatsapp)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /Stop the link/ })).toBeNull();
  });
});
