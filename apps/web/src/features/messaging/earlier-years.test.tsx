import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DuesScreen } from "@/features/fees/ReportsScreens";
import type { Dues } from "@/features/fees/data";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { READ, SEND, type FeeReminderPreview, type Settings } from "./data";

/**
 * FM-01 manual fee reminders with earlier-year dues (FR-FEE-034, FR-FEE-035): the reminder
 * dialog's "Include earlier-year dues" option (off by default; one message per parent with the
 * total), the 422 `earlier_year` refusal in plain language, and payment links for this year's
 * rows only. Synthetic values only.
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
const t = en.messaging.feeReminders;
const CURRENT = "0192f3a4-0000-7000-8000-00000000f401";
const EARLIER = "0192f3a4-0000-7000-8000-00000000f402";
const LEFT = "0192f3a4-0000-7000-8000-00000000f403";

let stub: BffStub;

function row(account: string, name: string, earlier: boolean): Dues["rows"][number] {
  return {
    account_id: account,
    student_id: `0192f3a4-0000-7000-8000-00000000e4${account.slice(-2)}`,
    display_name: name,
    admission_no: `F-04${account.slice(-2)}`,
    class_section: "Class VIII B",
    overdue: "2500.00",
    oldest_due_on: earlier ? "2025-08-15" : "2026-08-15",
    balance: "2500.00",
    last_payment_on: null,
    academic_year_id: earlier
      ? "0192f3a4-0000-7000-8000-00000000c402"
      : "0192f3a4-0000-7000-8000-00000000c401",
    academic_year_label: earlier ? "2025-26" : "2026-27",
    earlier_year: earlier,
  };
}

function dues(): Dues {
  return {
    as_of: "2026-11-09",
    students: 2,
    total_overdue: "7500.00",
    arrears_students: 2,
    arrears_total: "5000.00",
    rows: [
      row(EARLIER, "Synthetica Earlier", true),
      row(LEFT, "Synthetica Left", true),
      row(CURRENT, "Synthetica Current", false),
    ],
  };
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

function preview(): FeeReminderPreview {
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
  };
}

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(["finance.read", SEND, READ], { roles: ["principal"] }));
  stub.routes["GET /bff/api/v1/messaging/settings"] = () => Response.json(settings());
  stub.routes["GET /bff/api/v1/fees/dues"] = () => Response.json(dues());
  stub.routes["GET /bff/api/v1/classes"] = () => Response.json({ data: [], next_cursor: null });
  // Like the API: an earlier-year row without the option is refused (422 earlier_year).
  stub.routes["POST /bff/api/v1/fees/reminders/preview"] = async (request) => {
    const body = (await request.json()) as {
      account_ids?: string[];
      include_earlier_years: boolean;
    };
    const earlier = (body.account_ids ?? []).some((id) => id !== CURRENT);
    if (earlier && !body.include_earlier_years) {
      return problem(422, "validation_error", {
        errors: [
          { field: "account_ids", code: "earlier_year", message_key: "errors.earlier_year" },
        ],
      });
    }
    return Response.json(preview());
  };
  stub.routes["POST /bff/api/v1/fees/reminders"] = () =>
    Response.json({ queued: 1, broadcast_id: null }, { status: 202 });
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

async function openRowMenu(name: string) {
  await screen.findByText(name);
  await userEvent.click(
    screen.getByRole("button", { name: en.fees.dues.rowActions.replace("{name}", name) }),
  );
}

describe("Include earlier-year dues in a manual reminder (FR-FEE-035)", () => {
  it("is off by default, explains the refusal, and sends the total once ticked", async () => {
    renderWithIntl(<DuesScreen />);
    await openRowMenu("Synthetica Earlier");
    // Earlier years are paid at the office: no payment link for an earlier-year row.
    expect(
      screen.queryByRole("menuitem", { name: en.fees.dues.sendPaymentLink }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: en.fees.dues.sendReminder }));
    const dialog = await screen.findByRole("dialog", { name: t.title });
    const option = within(dialog).getByRole("checkbox", { name: new RegExp(t.includeEarlier) });
    expect(option).not.toBeChecked();
    expect(within(dialog).getByText(t.includeEarlierHint)).toBeInTheDocument();
    expect(await within(dialog).findByText(t.earlierYearTitle)).toBeInTheDocument();
    expect(within(dialog).getByText(t.earlierYearBody)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /Send/ })).toBeDisabled();

    await userEvent.click(option);
    const confirm = await within(dialog).findByRole("button", { name: "Send 1 reminder" });
    const previews = stub.callsTo("POST /bff/api/v1/fees/reminders/preview");
    expect(JSON.parse(previews.at(-1)?.body ?? "{}")).toEqual({
      account_ids: [EARLIER],
      all_matching: false,
      kind: "reminder",
      include_earlier_years: true,
    });
    expect(within(dialog).queryByText(t.earlierYearTitle)).toBeNull();
    await userEvent.click(confirm);
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/fees/reminders")).toHaveLength(1));
    const sent = stub.callsTo("POST /bff/api/v1/fees/reminders")[0];
    expect(sent?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(sent?.body ?? "{}")).toMatchObject({
      account_ids: [EARLIER],
      include_earlier_years: true,
      preview_token: preview().preview_token,
    });
  });

  it("starts unticked again for the next opening", async () => {
    renderWithIntl(<DuesScreen />);
    await openRowMenu("Synthetica Earlier");
    await userEvent.click(screen.getByRole("menuitem", { name: en.fees.dues.sendReminder }));
    let dialog = await screen.findByRole("dialog", { name: t.title });
    await userEvent.click(
      within(dialog).getByRole("checkbox", { name: new RegExp(t.includeEarlier) }),
    );
    await within(dialog).findByRole("button", { name: "Send 1 reminder" });
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await openRowMenu("Synthetica Left");
    await userEvent.click(screen.getByRole("menuitem", { name: en.fees.dues.sendReminder }));
    dialog = await screen.findByRole("dialog", { name: t.title });
    expect(
      within(dialog).getByRole("checkbox", { name: new RegExp(t.includeEarlier) }),
    ).not.toBeChecked();
  });

  it("offers no earlier-years option on a payment link", async () => {
    renderWithIntl(<DuesScreen />);
    await openRowMenu("Synthetica Current");
    await userEvent.click(screen.getByRole("menuitem", { name: en.fees.dues.sendPaymentLink }));
    const dialog = await screen.findByRole("dialog", { name: en.messaging.paymentLinks.title });
    await within(dialog).findByRole("button", { name: "Send 1 payment link" });
    expect(within(dialog).queryByRole("checkbox")).toBeNull();
    expect(
      JSON.parse(stub.callsTo("POST /bff/api/v1/fees/reminders/preview").at(-1)?.body ?? "{}"),
    ).toMatchObject({ kind: "payment_link", include_earlier_years: false });
  });
});
