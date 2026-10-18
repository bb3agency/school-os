import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me, SECTION, structureRoutes } from "@/test/school-fixtures";
import { BroadcastReportScreen } from "./BroadcastScreens";
import {
  READ,
  SEND,
  type Automatic,
  type Broadcast,
  type BroadcastPage,
  type BroadcastPreview,
  type BroadcastReport,
  type Settings,
} from "./data";
import { MessagesScreen } from "./MessagesScreen";
import { SendNoticePanel } from "./SendNoticePanel";

/**
 * WhatsApp sending screens (R2-3G; US-2204..US-2208; FR-MSG-012, FR-MSG-017): the notice's
 * "Send on WhatsApp" panel (preview, cost, consequence, confirm, live counts), the broadcasts and
 * automatic messages on /messages, and the delivery report. Synthetic values only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/notices",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.messaging;
const NOTICE = "0192f3a4-0000-7000-8000-00000000a001";
const BROADCAST = "0192f3a4-0000-7000-8000-00000000a101";

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  Object.assign(stub.routes, structureRoutes());
  stub.routes["GET /bff/api/v1/messaging/settings"] = () => Response.json(settings());
  stub.routes[`GET /bff/api/v1/notices/${NOTICE}/broadcasts`] = () =>
    Response.json({ items: [], next_cursor: null } satisfies BroadcastPage);
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["office_admin"] }));
}

function settings(): Settings {
  return {
    notices_enabled: true,
    absence_alerts_enabled: true,
    fee_reminders_enabled: false,
    fee_receipts_enabled: true,
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

function preview(overrides: Partial<BroadcastPreview> = {}): BroadcastPreview {
  return {
    preview_token: "bp_0192f3a400007000800000000000a101",
    notice_id: NOTICE,
    section_ids: [SECTION],
    guardians: 450,
    recipients: 412,
    skipped: { no_optin: 30, no_mobile_number: 8 },
    languages: { en: 412 },
    language_fallbacks: 0,
    already_sent: 0,
    estimated_cost_inr: "55.91",
    utility_rate_inr: "0.115",
    gst_percent: "18",
    blocked_reason: null,
    expires_at: "2026-10-18T06:30:00Z",
    ...overrides,
  };
}

function broadcast(overrides: Partial<Broadcast> = {}): Broadcast {
  return {
    id: BROADCAST,
    notice_id: NOTICE,
    notice_title: "Sports day",
    status: "sending",
    section_ids: [SECTION],
    resend: false,
    recipients: 412,
    skipped: { no_optin: 30 },
    languages: { en: 412 },
    language_fallbacks: 0,
    estimated_cost_inr: "55.91",
    queued: 412,
    counts: { queued: 12, sent: 100, delivered: 250, read: 40, failed: 10 },
    requested_by: "0192f3a4-0000-7000-8000-0000000000u1",
    confirmed_at: "2026-10-18T06:00:00Z",
    completed_at: null,
    created_at: "2026-10-18T05:59:00Z",
    ...overrides,
  };
}

function report(overrides: Partial<BroadcastReport> = {}): BroadcastReport {
  return {
    broadcast: broadcast(),
    sections: [
      { section_id: SECTION, label: "IX-A", counts: { delivered: 250, read: 40, failed: 10 } },
    ],
    failure_reasons: { not_on_whatsapp: 10 },
    ...overrides,
  };
}

describe("Send on WhatsApp (US-2204)", () => {
  it("previews recipients, skips and cost, then confirms once with the preview token", async () => {
    signedIn([SEND, READ]);
    stub.routes[`POST /bff/api/v1/notices/${NOTICE}/broadcasts/preview`] = () =>
      Response.json(preview());
    stub.routes[`POST /bff/api/v1/notices/${NOTICE}/broadcasts`] = () =>
      Response.json(broadcast(), { status: 202 });
    stub.routes[`GET /bff/api/v1/messaging/broadcasts/${BROADCAST}`] = () =>
      Response.json(report());
    renderWithIntl(<SendNoticePanel noticeId={NOTICE} />);
    const check = await screen.findByRole("button", { name: en.send.check });
    expect(check).toBeDisabled();
    await userEvent.click(await screen.findByRole("checkbox"));
    await userEvent.click(check);
    const card = await screen.findByRole("region", { name: en.send.previewLabel });
    expect(within(card).getByText("412")).toBeInTheDocument();
    expect(within(card).getByText("₹55.91")).toBeInTheDocument();
    expect(within(card).getByText(en.log.reasons.no_optin)).toBeInTheDocument();
    expect(within(card).getByText(en.send.consequence)).toBeInTheDocument();
    const body = JSON.parse(
      stub.callsTo(`POST /bff/api/v1/notices/${NOTICE}/broadcasts/preview`)[0]?.body ?? "{}",
    );
    expect(body).toEqual({ section_ids: [SECTION], resend: false });
    const confirm = within(card).getByRole("button", {
      name: en.send.confirm.replace("{count}", "412"),
    });
    expect(confirm).toHaveAccessibleDescription(en.send.consequence);
    await userEvent.click(confirm);
    expect(await screen.findByText(en.send.queued)).toBeInTheDocument();
    const sent = stub.callsTo(`POST /bff/api/v1/notices/${NOTICE}/broadcasts`);
    expect(sent).toHaveLength(1);
    expect(sent[0]?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(sent[0]?.body ?? "{}")).toEqual({
      preview_token: preview().preview_token,
      section_ids: [SECTION],
    });
    expect(
      await screen.findByText(
        en.broadcasts.deliveredOf.replace("{delivered}", "290").replace("{total}", "412"),
      ),
    ).toBeInTheDocument();
  });

  it("says why nothing can be sent and offers no confirm button", async () => {
    signedIn([SEND, READ]);
    stub.routes[`POST /bff/api/v1/notices/${NOTICE}/broadcasts/preview`] = () =>
      Response.json(preview({ blocked_reason: "messaging_paused" }));
    renderWithIntl(<SendNoticePanel noticeId={NOTICE} />);
    await userEvent.click(await screen.findByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: en.send.check }));
    expect(await screen.findByText(en.send.blockedTitle)).toBeInTheDocument();
    expect(screen.getByText(en.log.reasons.messaging_paused, { exact: false })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Send to/ })).toBeNull();
  });

  it("asks for a reason before sending again (US-2204 AC3)", async () => {
    signedIn([SEND, READ]);
    stub.routes[`POST /bff/api/v1/notices/${NOTICE}/broadcasts/preview`] = async (request) => {
      const asked = (await request.json()) as { resend: boolean };
      return Response.json(
        asked.resend
          ? preview({ recipients: 40, already_sent: 0 })
          : preview({ recipients: 0, already_sent: 40, skipped: { already_sent: 40 } }),
      );
    };
    stub.routes[`POST /bff/api/v1/notices/${NOTICE}/broadcasts`] = () =>
      Response.json(broadcast({ resend: true }), { status: 202 });
    stub.routes[`GET /bff/api/v1/messaging/broadcasts/${BROADCAST}`] = () =>
      Response.json(report());
    renderWithIntl(<SendNoticePanel noticeId={NOTICE} />);
    await userEvent.click(await screen.findByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: en.send.check }));
    expect(await screen.findByText(en.send.nobody)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("checkbox", { name: en.send.resend.replace("{count}", "40") }),
    );
    const confirm = await screen.findByRole("button", {
      name: en.send.confirm.replace("{count}", "40"),
    });
    await userEvent.click(confirm);
    expect(await screen.findByText(en.send.reasonTooShort)).toBeInTheDocument();
    expect(stub.callsTo(`POST /bff/api/v1/notices/${NOTICE}/broadcasts`)).toHaveLength(0);
    await userEvent.type(
      screen.getByLabelText(en.send.reason, { exact: false }),
      "The first message had the wrong date",
    );
    await userEvent.click(confirm);
    await screen.findByText(en.send.queued);
    expect(
      JSON.parse(stub.callsTo(`POST /bff/api/v1/notices/${NOTICE}/broadcasts`)[0]?.body ?? "{}"),
    ).toMatchObject({ resend_reason: "The first message had the wrong date" });
  });

  it("is not offered without message.send", async () => {
    signedIn([READ]);
    const { container } = renderWithIntl(<SendNoticePanel noticeId={NOTICE} />);
    await waitFor(() => expect(stub.callsTo("GET /bff/api/v1/me").length).toBeGreaterThan(0));
    expect(container.textContent).not.toContain(en.send.title);
  });
});

describe("broadcasts and automatic messages on /messages (FR-MSG-017)", () => {
  it("lists broadcasts with live delivery and the automatic runs", async () => {
    signedIn([READ]);
    stub.routes["GET /bff/api/v1/messaging/broadcasts"] = () =>
      Response.json({ items: [broadcast()], next_cursor: null } satisfies BroadcastPage);
    stub.routes["GET /bff/api/v1/messaging/automatic"] = () =>
      Response.json({
        absence_alerts_enabled: true,
        absence_alert_time: "11:00:00",
        fee_reminders_enabled: false,
        fee_reminder_time: "10:00:00",
        fee_receipts_enabled: true,
        no_message_dates: [],
        queued_now: 3,
        runs: [
          {
            job: "absence_alerts",
            run_on: "2026-10-18",
            started_at: "2026-10-18T05:30:00Z",
            finished_at: "2026-10-18T05:30:02Z",
            considered: 14,
            queued: 9,
            counts: { delivered: 7, read: 1, failed: 1 },
          },
        ],
      } satisfies Automatic);
    stub.routes["POST /bff/api/v1/messages/search"] = () =>
      Response.json({ items: [], next_cursor: null });
    renderWithIntl(<MessagesScreen />);
    const link = await screen.findByRole("link", { name: "Sports day" });
    expect(link).toHaveAttribute(
      "href",
      expect.stringContaining(`/messages/broadcasts/${BROADCAST}`),
    );
    expect(
      screen.getAllByText(
        en.broadcasts.deliveredOf.replace("{delivered}", "290").replace("{total}", "412"),
      ).length,
    ).toBeGreaterThan(0);
    expect((await screen.findAllByText(en.automatic.jobs.absence_alerts)).length).toBe(2);
    expect(screen.getByText(en.automatic.waiting.replace("{count}", "3"))).toBeInTheDocument();
  });

  it("hides the broadcast and automatic cards when the API refuses them (403)", async () => {
    signedIn([READ]);
    stub.routes["POST /bff/api/v1/messages/search"] = () =>
      Response.json({ items: [], next_cursor: null });
    stub.routes["GET /bff/api/v1/messaging/broadcasts"] = () =>
      Response.json({ code: "forbidden" }, { status: 403 });
    stub.routes["GET /bff/api/v1/messaging/automatic"] = () =>
      Response.json({ code: "forbidden" }, { status: 403 });
    renderWithIntl(<MessagesScreen />);
    expect(await screen.findByText(en.log.emptyTitle)).toBeInTheDocument();
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/messaging/broadcasts").length).toBeGreaterThan(0),
    );
    await waitFor(() => expect(screen.queryByText(en.broadcasts.title)).toBeNull());
  });
});

describe("delivery report (FR-MSG-017)", () => {
  it("shows counts by section and reasons in words, with no phone numbers", async () => {
    signedIn([READ]);
    stub.routes[`GET /bff/api/v1/messaging/broadcasts/${BROADCAST}`] = () =>
      Response.json(report({ broadcast: broadcast({ status: "done", completed_at: "x" }) }));
    renderWithIntl(<BroadcastReportScreen broadcastId={BROADCAST} />);
    expect(await screen.findByRole("heading", { name: "Sports day", level: 1 })).toBeVisible();
    expect(screen.getAllByText("IX-A").length).toBeGreaterThan(0);
    expect(screen.getByText(en.log.reasons.not_on_whatsapp)).toBeInTheDocument();
    expect(screen.getByText(en.broadcasts.finished)).toBeInTheDocument();
  });
});
