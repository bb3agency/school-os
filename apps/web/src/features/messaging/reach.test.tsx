import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me, SECTION, structureRoutes } from "@/test/school-fixtures";
import { ClosureScreen } from "./ClosureScreen";
import { READ, RECORD } from "./data";
import {
  CLOSURE_SEND,
  type Closure,
  type ClosurePage,
  type ClosurePreview,
  type Coverage,
  type FollowUp,
  type NotReached,
} from "./reach-data";
import { BroadcastFollowUpCard, ReachScreen } from "./ReachScreens";

/**
 * FP-01 (who WhatsApp reaches, FR-MSG-039..045) and FP-02 (urgent closure message,
 * FR-MSG-047..054). Synthetic values only; no phone number appears on any of these screens.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/messages/reach",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.messaging;
const STUDENT = "0192f3a4-0000-7000-8000-00000000b001";
const GUARDIAN = "0192f3a4-0000-7000-8000-00000000b101";
const BROADCAST = "0192f3a4-0000-7000-8000-00000000a101";
const CLOSURE = "0192f3a4-0000-7000-8000-00000000c101";

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  Object.assign(stub.routes, structureRoutes());
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[], role = "office_admin") {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions, { roles: [role] }));
}

function coverage(): Coverage {
  return {
    category: "notices",
    students: 40,
    students_reached: 30,
    guardians: 60,
    guardians_opted_in: 41,
    sections: [
      {
        section_id: SECTION,
        label: "IX-A",
        students: 40,
        students_reached: 30,
        guardians: 60,
        guardians_opted_in: 41,
        guardians_without_mobile: 3,
      },
    ],
  };
}

function notReached(): NotReached {
  return {
    section_id: SECTION,
    label: "IX-A",
    category: "notices",
    students: 40,
    truncated: false,
    items: [
      {
        student_id: STUDENT,
        name: "Synthetica Ravi",
        class_section: "IX-A",
        guardians: [
          { guardian_id: GUARDIAN, relationship: "Mother", is_primary: true, reason: "no_optin" },
        ],
      },
    ],
  };
}

describe("FP-01 who WhatsApp reaches", () => {
  it("FR-MSG-039/041/043: coverage per section, the missing families and their slips", async () => {
    signedIn([READ, RECORD]);
    stub.routes["GET /bff/api/v1/messaging/coverage"] = () => Response.json(coverage());
    stub.routes[`GET /bff/api/v1/sections/${SECTION}/optins/not-reached`] = () =>
      Response.json(notReached());
    renderWithIntl(<ReachScreen />);
    expect(await screen.findByText("30 of 40 students reached (75%)")).toBeInTheDocument();
    await userEvent.click(
      await screen.findByRole("button", {
        name: en.reach.seeMissing.replace("{section}", "IX-A"),
      }),
    );
    expect(await screen.findByText("Synthetica Ravi")).toBeInTheDocument();
    expect(screen.getByText(en.log.reasons.no_optin, { exact: false })).toBeInTheDocument();
    const print = screen.getByRole("link", { name: en.reach.printMissing });
    expect(print).toHaveAttribute(
      "href",
      `/bff/api/v1/sections/${SECTION}/join-slips?categories=notices&missing_only=true`,
    );
    const query = stub.callsTo("GET /bff/api/v1/messaging/coverage")[0]?.url.searchParams;
    expect(query?.get("category")).toBe("notices");
  });

  it("without message.read the screen says so and asks nothing", async () => {
    signedIn([RECORD]);
    renderWithIntl(<ReachScreen />);
    expect(await screen.findByText(en.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/messaging/coverage")).toHaveLength(0);
  });

  it("FR-MSG-044/045: the broadcast report lists the families to follow up", async () => {
    signedIn([READ]);
    const body: FollowUp = {
      broadcast_id: BROADCAST,
      notice_id: BROADCAST,
      not_opened_hours: 48,
      outcomes: { opened: 20, pending: 2, not_sent: 1, failed: 0, not_opened: 3 },
      truncated: false,
      items: [
        {
          student_id: STUDENT,
          name: "Synthetica Ravi",
          class_section: "IX-A",
          outcome: "not_opened",
          guardians: [
            { guardian_id: GUARDIAN, relationship: "Father", is_primary: true, reason: "sent" },
          ],
        },
      ],
    };
    stub.routes[`GET /bff/api/v1/messaging/broadcasts/${BROADCAST}/follow-up`] = () =>
      Response.json(body);
    renderWithIntl(<BroadcastFollowUpCard broadcastId={BROADCAST} />);
    expect(await screen.findByText("Synthetica Ravi")).toBeInTheDocument();
    expect(screen.getAllByText(en.reach.followUp.outcomes.not_opened).length).toBeGreaterThan(0);
  });
});

function closurePreview(): ClosurePreview {
  return {
    preview_token: "cl_" + "a".repeat(40),
    guardians: 500,
    recipients: 420,
    skipped: { no_optin: 80 },
    languages: { en: 420 },
    language_fallbacks: 0,
    estimated_cost_inr: "56.99",
    utility_rate_inr: "0.115",
    gst_percent: "18",
    message_en:
      "Urgent update from Synthetic School: the school will be closed on 10/11/2026 because of heavy rain or a storm warning. Please contact the school office if you have questions.",
    sends_at: null,
    window_start: "06:00:00",
    window_end: "21:30:00",
    blocked_reason: null,
    recent_closures: 0,
  };
}

function closure(): Closure {
  return {
    id: CLOSURE,
    whole_school: true,
    section_ids: [],
    closed_from: "2026-11-10",
    closed_to: "2026-11-10",
    reason: "weather",
    recipients: 420,
    queued: 420,
    skipped: { no_optin: 80 },
    languages: { en: 420 },
    estimated_cost_inr: "56.99",
    counts: { queued: 420 },
    sent_by: GUARDIAN,
    sent_at: "2026-11-09T16:30:00Z",
  };
}

function closurePage(items: Closure[] = []): ClosurePage {
  return {
    items,
    uses: items.length,
    uses_window_days: 120,
    window_start: "06:00:00",
    window_end: "21:30:00",
    reasons: ["weather", "government_order", "local_situation", "other"],
  };
}

describe("FP-02 urgent closure message", () => {
  it("FR-MSG-048/049: the principal checks the exact message, then sends it once", async () => {
    signedIn([READ, CLOSURE_SEND], "principal");
    stub.routes["GET /bff/api/v1/messaging/closures"] = () => Response.json(closurePage());
    stub.routes["POST /bff/api/v1/messaging/closures/preview"] = () =>
      Response.json(closurePreview());
    stub.routes["POST /bff/api/v1/messaging/closures"] = () =>
      Response.json(closure(), { status: 202 });
    renderWithIntl(<ClosureScreen />);
    await userEvent.click(await screen.findByRole("button", { name: en.closures.check }));
    expect(await screen.findByText(closurePreview().message_en)).toBeInTheDocument();
    expect(screen.getByText(en.closures.sendsNow, { exact: false })).toBeInTheDocument();
    const preview = JSON.parse(
      stub.callsTo("POST /bff/api/v1/messaging/closures/preview")[0]?.body ?? "{}",
    );
    expect(preview).toMatchObject({ whole_school: true, reason: "weather" });
    expect(preview).not.toHaveProperty("section_ids");
    await userEvent.click(screen.getByRole("button", { name: "Send to 420 guardians" }));
    expect(await screen.findByText("420 messages are on their way.")).toBeInTheDocument();
    const sent = stub.callsTo("POST /bff/api/v1/messaging/closures");
    expect(sent).toHaveLength(1);
    expect(sent[0]?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(sent[0]?.body ?? "{}")).toMatchObject({
      preview_token: closurePreview().preview_token,
      whole_school: true,
    });
  });

  it("FR-MSG-052/054: other readers see the recent closures, not the form", async () => {
    signedIn([READ]);
    stub.routes["GET /bff/api/v1/messaging/closures"] = () =>
      Response.json(closurePage([closure()]));
    renderWithIntl(<ClosureScreen />);
    expect(await screen.findByText(en.closures.principalOnly)).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: en.closures.recentTitle });
    await waitFor(() =>
      expect(within(table).getByText(en.closures.reasons.weather)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: en.closures.check })).not.toBeInTheDocument();
  });
});

describe("FP-02 follow-up: order text and the school reopens (FR-MSG-056..064)", () => {
  it("FR-MSG-056: the order text goes with the preview, trimmed", async () => {
    signedIn([READ, CLOSURE_SEND], "principal");
    stub.routes["GET /bff/api/v1/messaging/closures"] = () => Response.json(closurePage());
    stub.routes["POST /bff/api/v1/messaging/closures/preview"] = () =>
      Response.json(closurePreview());
    renderWithIntl(<ClosureScreen />);
    await userEvent.type(
      await screen.findByLabelText(en.closures.noticeText),
      "  Collector's order: schools closed.  ",
    );
    await userEvent.click(screen.getByRole("button", { name: en.closures.check }));
    expect(await screen.findByText(en.closures.pageNote)).toBeInTheDocument();
    const preview = JSON.parse(
      stub.callsTo("POST /bff/api/v1/messaging/closures/preview")[0]?.body ?? "{}",
    );
    expect(preview.notice_text).toBe("Collector's order: schools closed.");
  });

  it("FR-MSG-060/061: the principal previews and sends one reopening message", async () => {
    signedIn([READ, CLOSURE_SEND], "principal");
    stub.routes["GET /bff/api/v1/messaging/closures"] = () =>
      Response.json(closurePage([closure()]));
    const reopening = {
      id: "0192f3a4-0000-7000-8000-00000000c201",
      closure_id: CLOSURE,
      reopens_on: "2026-11-11",
      recipients: 420,
      queued: 420,
      skipped: {},
      languages: { en: 420 },
      estimated_cost_inr: "56.99",
      counts: { queued: 420 },
      sent_by: GUARDIAN,
      sent_at: "2026-11-10T16:30:00Z",
    };
    stub.routes[`POST /bff/api/v1/messaging/closures/${CLOSURE}/reopening/preview`] = () =>
      Response.json({
        preview_token: "ro_" + "b".repeat(40),
        closure_id: CLOSURE,
        reopens_on: "2026-11-11",
        guardians: 500,
        recipients: 420,
        skipped: {},
        languages: { en: 420 },
        language_fallbacks: 0,
        estimated_cost_inr: "56.99",
        message_en: "Update from Synthetic School: the school will reopen on 11/11/2026.",
        sends_at: null,
        window_start: "06:00:00",
        window_end: "21:30:00",
        blocked_reason: null,
      });
    stub.routes[`POST /bff/api/v1/messaging/closures/${CLOSURE}/reopening`] = () =>
      Response.json(reopening, { status: 202 });
    renderWithIntl(<ClosureScreen />);
    await userEvent.click(await screen.findByRole("button", { name: en.closures.reopen.action }));
    await userEvent.click(await screen.findByRole("button", { name: en.closures.reopen.check }));
    expect(
      await screen.findByText(
        "Update from Synthetic School: the school will reopen on 11/11/2026.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Send to 420 guardians" }));
    expect(await screen.findByText("420 messages are on their way.")).toBeInTheDocument();
    const sent = stub.callsTo(`POST /bff/api/v1/messaging/closures/${CLOSURE}/reopening`);
    expect(sent).toHaveLength(1);
    expect(sent[0]?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(sent[0]?.body ?? "{}")).toEqual({
      reopens_on: "2026-11-11",
      preview_token: "ro_" + "b".repeat(40),
    });
  });

  it("FR-MSG-064: a sent reopening shows its date; readers get no button", async () => {
    signedIn([READ]);
    stub.routes["GET /bff/api/v1/messaging/closures"] = () =>
      Response.json(
        closurePage([
          {
            ...closure(),
            reopening: {
              id: "0192f3a4-0000-7000-8000-00000000c201",
              closure_id: CLOSURE,
              reopens_on: "2026-11-11",
              recipients: 1,
              queued: 1,
              skipped: {},
              languages: {},
              estimated_cost_inr: "0.14",
              counts: {},
              sent_by: GUARDIAN,
              sent_at: "2026-11-10T16:30:00Z",
            },
          },
        ]),
      );
    renderWithIntl(<ClosureScreen />);
    const table = await screen.findByRole("table", { name: en.closures.recentTitle });
    await waitFor(() => expect(within(table).getByText(/^Reopens /)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: en.closures.reopen.action })).toBeNull();
  });
});
