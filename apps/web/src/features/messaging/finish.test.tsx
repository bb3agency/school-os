import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NoticeDetailScreen } from "@/features/circulars/NoticeDetailScreen";
import type { Notice } from "@/features/circulars/data";
import { FeeSettingsScreen } from "@/features/fees/FeeSettingsScreen";
import { DuesScreen } from "@/features/fees/ReportsScreens";
import type { Dues } from "@/features/fees/data";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import {
  READ,
  SEND,
  type FeeReminderPreview,
  type ReceiptWhatsApp as ReceiptState,
  type Settings,
} from "./data";
import { ReceiptWhatsApp } from "./ReceiptWhatsApp";

/**
 * R2-3J messaging finish-up screens: "Send reminder" from the dues list (FR-MSG-025), "Send on
 * WhatsApp" for a receipt (FR-MSG-026), the reminder days in the fee settings (FR-MSG-014) and
 * withdrawing a notice (FR-NOTICE-009). Synthetic values only.
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
const RECEIPT = "0192f3a4-0000-7000-8000-00000000f201";
const ACCOUNT_A = "0192f3a4-0000-7000-8000-00000000f001";
const ACCOUNT_B = "0192f3a4-0000-7000-8000-00000000f002";
const STUDENT_A = "0192f3a4-0000-7000-8000-00000000e001";
const STUDENT_B = "0192f3a4-0000-7000-8000-00000000e002";
const NOTICE = "0192f3a4-0000-7000-8000-00000000a111";

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

function receiptState(overrides: Partial<ReceiptState> = {}): ReceiptState {
  return {
    receipt_id: RECEIPT,
    can_send: true,
    blocked_reason: null,
    opted_in_guardians: 1,
    messages: [],
    ...overrides,
  };
}

describe("Send on WhatsApp for a receipt (FR-MSG-026)", () => {
  it("sends once with an idempotency key and shows the status", async () => {
    signedIn(["fee.collect"]);
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}/whatsapp`] = () =>
      Response.json(receiptState());
    stub.routes[`POST /bff/api/v1/fees/receipts/${RECEIPT}/whatsapp`] = () =>
      Response.json(
        receiptState({
          can_send: false,
          blocked_reason: "already_queued",
          messages: [
            {
              id: "0192f3a4-0000-7000-8000-00000000m001",
              status: "queued",
              skip_reason: null,
              failure_reason: null,
              language: "en",
              queued_at: "2026-10-21T05:00:00Z",
              status_at: "2026-10-21T05:00:00Z",
            },
          ],
        }),
        { status: 202 },
      );
    renderWithIntl(<ReceiptWhatsApp receiptId={RECEIPT} />);
    const send = await screen.findByRole("button", { name: en.messaging.receipt.send });
    expect(send).not.toHaveFocus();
    expect(screen.getByText("To the guardian who agreed to fee messages.")).toBeInTheDocument();
    await userEvent.click(send);
    expect(await screen.findByText(en.messaging.receipt.queued)).toBeInTheDocument();
    const posts = stub.callsTo(`POST /bff/api/v1/fees/receipts/${RECEIPT}/whatsapp`);
    expect(posts).toHaveLength(1);
    expect(posts[0]?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: en.messaging.receipt.send })).toBeNull(),
    );
  });

  it("says when no guardian agreed and offers no button", async () => {
    signedIn(["fee.collect"]);
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}/whatsapp`] = () =>
      Response.json(
        receiptState({
          can_send: false,
          blocked_reason: "no_opted_in_guardian",
          opted_in_guardians: 0,
        }),
      );
    renderWithIntl(<ReceiptWhatsApp receiptId={RECEIPT} />);
    expect(await screen.findByText(en.messaging.receipt.noOptin)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("is hidden where WhatsApp is off (404) or not connected", async () => {
    signedIn(["fee.collect"]);
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}/whatsapp`] = () =>
      problem(404, "module_unavailable");
    const { container, unmount } = renderWithIntl(<ReceiptWhatsApp receiptId={RECEIPT} />);
    await waitFor(() =>
      expect(
        stub.callsTo(`GET /bff/api/v1/fees/receipts/${RECEIPT}/whatsapp`).length,
      ).toBeGreaterThan(0),
    );
    expect(container.textContent).toBe("");
    unmount();
    stub.routes[`GET /bff/api/v1/fees/receipts/${RECEIPT}/whatsapp`] = () =>
      Response.json(receiptState({ can_send: false, blocked_reason: "messaging_not_connected" }));
    const second = renderWithIntl(<ReceiptWhatsApp receiptId={RECEIPT} />);
    await waitFor(() =>
      expect(
        stub.callsTo(`GET /bff/api/v1/fees/receipts/${RECEIPT}/whatsapp`).length,
      ).toBeGreaterThan(1),
    );
    expect(second.container.textContent).toBe("");
  });
});

function dues(): Dues {
  return {
    as_of: "2026-10-21",
    students: 2,
    total_overdue: "7000.00",
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
      {
        account_id: ACCOUNT_B,
        student_id: STUDENT_B,
        display_name: "Synthetica Dues B",
        admission_no: "F-0002",
        class_section: "Class IX A",
        overdue: "3500.00",
        oldest_due_on: "2026-09-21",
        balance: "7000.00",
        last_payment_on: null,
      },
    ],
  };
}

function reminderPreview(overrides: Partial<FeeReminderPreview> = {}): FeeReminderPreview {
  return {
    preview_token: "fr_" + "a".repeat(40),
    accounts: 1,
    accounts_reminded: 1,
    guardians: 1,
    recipients: 1,
    skipped_accounts: {},
    skipped_guardians: { no_optin: 1 },
    estimated_cost_inr: "0.14",
    utility_rate_inr: "0.115",
    gst_percent: "18",
    blocked_reason: null,
    ...overrides,
  };
}

describe("Send reminder from the dues list (FR-MSG-025)", () => {
  beforeEach(() => {
    stub.routes["GET /bff/api/v1/fees/dues"] = () => Response.json(dues());
    stub.routes["GET /bff/api/v1/classes"] = () => Response.json({ data: [], next_cursor: null });
  });

  it("previews the chosen rows, then sends with the preview token", async () => {
    signedIn(["finance.read", SEND, READ]);
    stub.routes["POST /bff/api/v1/fees/reminders/preview"] = () => Response.json(reminderPreview());
    stub.routes["POST /bff/api/v1/fees/reminders"] = () =>
      Response.json(
        { accounts: 1, recipients: 1, queued: 1, skipped_accounts: {}, skipped_guardians: {} },
        { status: 202 },
      );
    renderWithIntl(<DuesScreen />);
    const box = await screen.findByRole("checkbox", { name: "Select Synthetica Dues A" });
    await userEvent.click(box);
    const bar = screen.getByRole("region", { name: en.ui.table.bulkActions });
    await userEvent.click(within(bar).getByRole("button", { name: en.fees.dues.sendReminder }));
    const dialog = await screen.findByRole("dialog", { name: en.messaging.feeReminders.title });
    expect(await within(dialog).findByText(en.messaging.log.reasons.no_optin)).toBeInTheDocument();
    expect(within(dialog).getByText("₹0.14")).toBeInTheDocument();
    expect(
      JSON.parse(stub.callsTo("POST /bff/api/v1/fees/reminders/preview")[0]?.body ?? "{}"),
    ).toEqual({ account_ids: [ACCOUNT_A], all_matching: false });
    const confirm = within(dialog).getByRole("button", { name: "Send 1 reminder" });
    expect(confirm).toHaveAccessibleDescription(en.messaging.feeReminders.consequence);
    await userEvent.click(confirm);
    expect(
      await within(dialog).findByText("1 reminder is queued. It goes out outside quiet hours."),
    ).toBeInTheDocument();
    const sent = stub.callsTo("POST /bff/api/v1/fees/reminders");
    expect(sent).toHaveLength(1);
    expect(sent[0]?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(sent[0]?.body ?? "{}")).toEqual({
      account_ids: [ACCOUNT_A],
      all_matching: false,
      preview_token: reminderPreview().preview_token,
    });
  });

  it("keeps the send button disabled when sending is blocked", async () => {
    signedIn(["finance.read", SEND, READ]);
    stub.routes["POST /bff/api/v1/fees/reminders/preview"] = () =>
      Response.json(reminderPreview({ blocked_reason: "messaging_paused" }));
    renderWithIntl(<DuesScreen />);
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Select Synthetica Dues B" }),
    );
    await userEvent.click(screen.getByRole("button", { name: en.fees.dues.sendReminder }));
    const dialog = await screen.findByRole("dialog", { name: en.messaging.feeReminders.title });
    expect(await within(dialog).findByText(en.messaging.send.blockedTitle)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Send 1 reminder" })).toBeDisabled();
  });

  it("offers no selection without message.send", async () => {
    signedIn(["finance.read"]);
    renderWithIntl(<DuesScreen />);
    expect(await screen.findByText("Synthetica Dues A")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Select Synthetica Dues A" })).toBeNull();
  });
});

describe("fee reminder days (FR-MSG-014)", () => {
  const current = {
    paper_size: "A5" as const,
    gst_note: "School fees are exempt from GST.",
    reminder_offsets: [-3, 0, 7],
    min_reminder_amount: "0.00",
    version: 4,
  };

  it("edits the days, refuses a repeated day and saves with If-Match", async () => {
    signedIn(["fee.manage", "finance.read", READ]);
    stub.routes["GET /bff/api/v1/fees/settings"] = () => Response.json(current);
    stub.routes["PUT /bff/api/v1/fees/settings"] = async (request) =>
      Response.json({ ...current, ...(await request.json()), version: 5 });
    renderWithIntl(<FeeSettingsScreen />);
    expect(await screen.findByText(en.fees.settings.remindersOff)).toBeInTheDocument();
    const days = await screen.findAllByLabelText(en.fees.settings.days);
    expect(days.map((input) => (input as HTMLInputElement).value)).toEqual(["3", "7"]);
    await userEvent.clear(days[1] as HTMLElement);
    await userEvent.type(days[1] as HTMLElement, "3");
    const whens = screen.getAllByLabelText(en.fees.settings.when);
    await userEvent.selectOptions(whens[2] as HTMLElement, "before");
    await userEvent.click(screen.getByRole("button", { name: en.fees.settings.save }));
    expect(await screen.findAllByText(en.fees.settings.errors.repeated)).not.toHaveLength(0);
    expect(stub.callsTo("PUT /bff/api/v1/fees/settings")).toHaveLength(0);
    await userEvent.clear(days[1] as HTMLElement);
    await userEvent.type(days[1] as HTMLElement, "10");
    await userEvent.click(screen.getByRole("button", { name: en.fees.settings.save }));
    expect(await screen.findByText(en.fees.settings.saved)).toBeInTheDocument();
    const put = stub.callsTo("PUT /bff/api/v1/fees/settings")[0];
    expect(put?.headers.get("If-Match")).toBe('W/"4"');
    expect(JSON.parse(put?.body ?? "{}")).toMatchObject({ reminder_offsets: [-3, 0, -10] });
  });

  it("shows the days read-only to a finance reader", async () => {
    signedIn(["finance.read"]);
    stub.routes["GET /bff/api/v1/fees/settings"] = () => Response.json(current);
    renderWithIntl(<FeeSettingsScreen />);
    expect(await screen.findByText("3 days before")).toBeInTheDocument();
    expect(screen.getByText("7 days after")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.fees.settings.save })).toBeNull();
  });
});

function approvedNotice(overrides: Partial<Notice> = {}): Notice {
  return {
    id: NOTICE,
    source: "blank",
    document_id: null,
    status: "approved",
    ai_drafted: false,
    draft_error: null,
    title_en: "Sports day",
    body_en: "Sports day is on 14/11/2026.",
    title_te: "",
    body_te: "",
    created_by: null,
    approved_by: null,
    approved_at: "2026-10-02T04:30:00Z",
    withdrawn_by: null,
    withdrawn_at: null,
    withdraw_reason: null,
    render_status: "ready",
    render_error: null,
    files_available: false,
    created_at: "2026-10-02T04:30:00Z",
    updated_at: "2026-10-02T04:30:00Z",
    version: 3,
    ...overrides,
  };
}

describe("withdraw a notice (FR-NOTICE-009)", () => {
  it("asks for a reason, sends If-Match and shows the withdrawn notice", async () => {
    signedIn(["notice.draft", "notice.approve"]);
    let current = approvedNotice();
    stub.routes[`GET /bff/api/v1/notices/${NOTICE}`] = () => Response.json(current);
    stub.routes[`GET /bff/api/v1/notices/${NOTICE}/broadcasts`] = () =>
      Response.json({ items: [], next_cursor: null });
    stub.routes[`POST /bff/api/v1/notices/${NOTICE}/withdraw`] = () => {
      current = approvedNotice({
        status: "withdrawn",
        withdrawn_at: "2026-10-21T05:00:00Z",
        withdraw_reason: "The date in this notice was wrong",
        version: 4,
      });
      return Response.json(current);
    };
    renderWithIntl(<NoticeDetailScreen noticeId={NOTICE} />);
    await userEvent.click(await screen.findByRole("button", { name: en.notices.withdraw.action }));
    const dialog = await screen.findByRole("dialog", { name: en.notices.withdraw.title });
    expect(within(dialog).getByText(en.notices.withdraw.consequence)).toBeInTheDocument();
    await userEvent.type(
      within(dialog).getByLabelText(en.notices.withdraw.reason, { exact: false }),
      "The date in this notice was wrong",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: en.notices.withdraw.action }));
    expect(
      await screen.findByText("Reason: The date in this notice was wrong", { exact: false }),
    ).toBeInTheDocument();
    const post = stub.callsTo(`POST /bff/api/v1/notices/${NOTICE}/withdraw`)[0];
    expect(post?.headers.get("If-Match")).toBe('W/"3"');
    expect(JSON.parse(post?.body ?? "{}")).toEqual({
      reason: "The date in this notice was wrong",
    });
    expect(screen.getAllByText(en.notices.status.withdrawn).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: en.notices.withdraw.action })).toBeNull();
  });

  it("is not offered without notice.approve", async () => {
    signedIn(["notice.draft"]);
    stub.routes[`GET /bff/api/v1/notices/${NOTICE}`] = () => Response.json(approvedNotice());
    renderWithIntl(<NoticeDetailScreen noticeId={NOTICE} />);
    expect(await screen.findByText("Sports day is on 14/11/2026.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.notices.withdraw.action })).toBeNull();
  });
});
