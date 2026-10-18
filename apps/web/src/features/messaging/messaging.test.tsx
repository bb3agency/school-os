import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WhatsAppSettingsPage from "@/app/[locale]/(school)/settings/whatsapp/page";
import {
  buildContentSecurityPolicy,
  EMBEDDED_SIGNUP_PATH,
  securityHeaders,
} from "@/lib/security-headers";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me, STUDENT } from "@/test/school-fixtures";
import {
  CONFIGURE,
  READ,
  RECORD,
  estimatedCost,
  sectionSlipsHref,
  settingsBody,
  type Account,
  type GuardianOptins,
  type MessagePage,
  type Settings,
} from "./data";
import { parseSignupMessage } from "./embedded-signup";
import { GuardianOptinsCard } from "./GuardianOptins";
import { MessagesScreen } from "./MessagesScreen";
import { connectStep } from "./WhatsAppSettingsScreen";

/**
 * WhatsApp messaging screens (R2-1C; ADR-0043; US-2201, US-2202, US-2208, US-2209):
 * connect, account state and pause, templates, settings, guardian opt-ins and the delivery log.
 * Synthetic values only; phones appear only as their last four digits.
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
const te = messages.te.messaging;
const GUARDIAN = "0192f3a4-0000-7000-8000-00000000f001";
const ACCOUNT = "0192f3a4-0000-7000-8000-00000000f101";

function account(overrides: Partial<Account> = {}): Account {
  return {
    connected: false,
    available: true,
    manual_token_allowed: true,
    embedded_signup: null,
    inr_required_from: "2027-01-01",
    templates: [],
    unreviewed_texts: [],
    version: 0,
    ...overrides,
  };
}

function connected(overrides: Partial<Account> = {}): Account {
  return account({
    connected: true,
    id: ACCOUNT,
    mode: "manual_token",
    status: "active",
    display_phone: "+91 90000 01234",
    display_name: "Synthetic Model School",
    quality_rating: "GREEN",
    messaging_limit: "TIER_250",
    messaging_limit_count: 250,
    default_daily_cap: 225,
    currency: "INR",
    currency_state: "confirmed_inr",
    templates: [
      {
        template_key: "school_notice",
        template_version: 1,
        language: "en",
        provider_name: "sos_school_notice_v1_en",
        status: "approved",
        provider_category: "UTILITY",
        paused_by_sos: false,
        rejected_reason: null,
        reviewed: true,
        usable: true,
        last_synced_at: null,
      },
      {
        template_key: "absence_alert",
        template_version: 1,
        language: "te",
        provider_name: "sos_absence_alert_v1_te",
        status: "approved",
        provider_category: "MARKETING",
        paused_by_sos: true,
        rejected_reason: null,
        reviewed: false,
        usable: false,
        last_synced_at: null,
      },
    ],
    version: 3,
    ...overrides,
  });
}

function settings(overrides: Partial<Settings> = {}): Settings {
  return {
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
    utility_rate_inr: "0.115",
    gst_percent: "18",
    version: 0,
    ...overrides,
  };
}

function optins(overrides: Partial<GuardianOptins> = {}): GuardianOptins {
  return {
    id: GUARDIAN,
    guardian_id: GUARDIAN,
    has_mobile: true,
    phone_last4: "1234",
    preferred_language: null,
    effective_language: "en",
    categories: [
      {
        category: "notices",
        status: "opted_out",
        method: "whatsapp_stop",
        decided_on: "2026-10-10",
        stopped_by_parent: true,
        for_current_number: false,
      },
      {
        category: "attendance",
        status: "opted_in",
        method: "in_person",
        decided_on: "2026-10-01",
        stopped_by_parent: false,
        for_current_number: true,
      },
      {
        category: "fees",
        status: "not_asked",
        method: null,
        decided_on: null,
        stopped_by_parent: false,
        for_current_number: false,
      },
    ],
    history: [],
    version: 2,
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
  it("accepts Embedded Signup results only from Meta's origin and shape", () => {
    const finish = {
      type: "WA_EMBEDDED_SIGNUP",
      event: "FINISH",
      data: { waba_id: "1234567890", phone_number_id: "9876543210", business_id: "555" },
    };
    expect(parseSignupMessage("https://www.facebook.com", JSON.stringify(finish))).toEqual({
      kind: "finish",
      ids: { waba_id: "1234567890", phone_number_id: "9876543210", business_id: "555" },
    });
    expect(parseSignupMessage("https://evil.example", finish).kind).toBe("ignore");
    expect(parseSignupMessage("https://facebook.com.evil.example", finish).kind).toBe("ignore");
    expect(
      parseSignupMessage("https://web.facebook.com", { ...finish, data: { waba_id: "x" } }).kind,
    ).toBe("error");
    expect(
      parseSignupMessage("https://www.facebook.com", {
        type: "WA_EMBEDDED_SIGNUP",
        event: "CANCEL",
      }).kind,
    ).toBe("cancel");
  });

  it("builds settings bodies, the slip link and the cost estimate", () => {
    expect(
      settingsBody({
        notices_enabled: true,
        absence_alerts_enabled: false,
        fee_reminders_enabled: false,
        fee_receipts_enabled: true,
        absence_alert_time: "11:00",
        quiet_start: "21:00",
        quiet_end: "07:00",
        daily_cap: "",
        office_phone: "",
      }),
    ).toMatchObject({ absence_alert_time: "11:00:00", daily_cap: null, office_phone: null });
    expect(sectionSlipsHref("s-1", ["notices", "fees"])).toBe(
      "/bff/api/v1/sections/s-1/join-slips?categories=notices&categories=fees",
    );
    expect(estimatedCost(412, "0.115", "18")).toBe(55.91);
  });

  it("allows Meta's SDK and popups on the WhatsApp settings page only", () => {
    const base = { nonce: "abc", isDev: false, upgradeInsecureRequests: true };
    expect(buildContentSecurityPolicy(base)).not.toContain("facebook");
    const meta = buildContentSecurityPolicy({ ...base, embeddedSignup: true });
    expect(meta).toContain("https://connect.facebook.net");
    expect(meta).toContain("frame-src https://www.facebook.com");
    expect(meta).toContain("frame-ancestors 'none'");
    expect(EMBEDDED_SIGNUP_PATH.test("/settings/whatsapp")).toBe(true);
    expect(EMBEDDED_SIGNUP_PATH.test("/te/settings/whatsapp")).toBe(true);
    expect(EMBEDDED_SIGNUP_PATH.test("/settings/whatsapp/../users")).toBe(false);
    expect(securityHeaders({ csp: "x", hsts: false })["Cross-Origin-Opener-Policy"]).toBe(
      "same-origin",
    );
    expect(
      securityHeaders({ csp: "x", hsts: false, allowPopups: true })["Cross-Origin-Opener-Policy"],
    ).toBe("same-origin-allow-popups");
  });

  it("has every messaging message in Telugu too", () => {
    const keys = (value: unknown, prefix = ""): string[] =>
      value && typeof value === "object"
        ? Object.entries(value).flatMap(([k, v]) => keys(v, `${prefix}${k}.`))
        : [prefix];
    expect(keys(te).sort()).toEqual(keys(en).sort());
  });
});

describe("WhatsApp settings (US-2201, US-2209)", () => {
  it("says WhatsApp is not offered here (404 module_unavailable)", async () => {
    signedIn([CONFIGURE, READ]);
    stub.routes["GET /bff/api/v1/messaging/account"] = () => problem(404, "module_unavailable");
    renderWithIntl(<WhatsAppSettingsPage />);
    expect(await screen.findByText(en.off.title)).toBeInTheDocument();
  });

  it("shows the checklist and connects with a pilot token", async () => {
    signedIn([CONFIGURE, READ]);
    stub.routes["GET /bff/api/v1/messaging/account"] = () => Response.json(account());
    stub.routes["GET /bff/api/v1/messaging/settings"] = () => Response.json(settings());
    stub.routes["POST /bff/api/v1/messaging/account/manual"] = () =>
      Response.json(connected(), { status: 201 });
    renderWithIntl(<WhatsAppSettingsPage />);
    expect(await screen.findByText(en.connect.paysMeta)).toBeInTheDocument();
    expect(screen.getByText(en.connect.inr)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: en.connect.manual }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(messages.en.common.stepUpNote)).toBeInTheDocument();
    await userEvent.type(
      within(dialog).getByLabelText(new RegExp(en.connect.token)),
      "synthetic-token-0123456789-abc",
    );
    await userEvent.type(within(dialog).getByLabelText(new RegExp(en.connect.wabaId)), "1234567");
    await userEvent.type(
      within(dialog).getByLabelText(new RegExp(en.connect.phoneNumberId)),
      "7654321",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: en.connect.connect }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/messaging/account/manual")).toHaveLength(1),
    );
    const call = stub.callsTo("POST /bff/api/v1/messaging/account/manual")[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      token: "synthetic-token-0123456789-abc",
      waba_id: "1234567",
      phone_number_id: "7654321",
    });
    expect(call?.url.search).toBe("");
  });

  it("shows a paused account with the reason and lets the owner resume", async () => {
    signedIn([CONFIGURE, READ]);
    stub.routes["GET /bff/api/v1/messaging/account"] = () =>
      Response.json(connected({ status: "paused", pause_reason: "quality_flagged" }));
    stub.routes["GET /bff/api/v1/messaging/settings"] = () => Response.json(settings());
    stub.routes["POST /bff/api/v1/messaging/account/resume"] = () =>
      problem(409, "messaging_still_restricted");
    renderWithIntl(<WhatsAppSettingsPage />);
    expect(await screen.findByText(en.account.pausedTitle)).toBeInTheDocument();
    expect(screen.getByText(en.account.pause.quality_flagged)).toBeInTheDocument();
    expect(screen.getByText(en.templates.pausedBySchoolOs)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: en.account.resume }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: en.account.resume }));
    expect(await screen.findByText(en.errors.messaging_still_restricted.title)).toBeInTheDocument();
  });

  it("saves settings with If-Match", async () => {
    signedIn([CONFIGURE, READ]);
    stub.routes["GET /bff/api/v1/messaging/account"] = () => Response.json(connected());
    stub.routes["GET /bff/api/v1/messaging/settings"] = () =>
      Response.json(settings({ version: 4 }));
    stub.routes["PUT /bff/api/v1/messaging/settings"] = () =>
      Response.json(settings({ version: 5 }));
    renderWithIntl(<WhatsAppSettingsPage />);
    const phone = await screen.findByLabelText(new RegExp(en.settings.officePhone));
    await userEvent.type(phone, "9000011111");
    await userEvent.click(screen.getByRole("button", { name: en.settings.save }));
    await waitFor(() => expect(stub.callsTo("PUT /bff/api/v1/messaging/settings")).toHaveLength(1));
    const call = stub.callsTo("PUT /bff/api/v1/messaging/settings")[0];
    expect(call?.headers.get("If-Match")).toBe('W/"4"');
    expect(JSON.parse(call?.body ?? "{}")).toMatchObject({
      office_phone: "9000011111",
      notices_enabled: false,
      quiet_start: "21:00:00",
    });
  });

  it("shows readers the state without change buttons", async () => {
    signedIn([READ]);
    stub.routes["GET /bff/api/v1/messaging/account"] = () => Response.json(connected());
    stub.routes["GET /bff/api/v1/messaging/settings"] = () => Response.json(settings());
    renderWithIntl(<WhatsAppSettingsPage />);
    expect(await screen.findByText("+91 90000 01234")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.account.disconnect })).toBeNull();
    expect(screen.queryByRole("button", { name: en.settings.save })).toBeNull();
  });
});

describe("guardian opt-ins (US-2202, US-2203)", () => {
  it("shows each category, a parent's STOP and a changed number, and records a decision", async () => {
    signedIn([RECORD, READ, "student.update_nonidentity"]);
    stub.routes[`GET /bff/api/v1/guardians/${GUARDIAN}/optins`] = () => Response.json(optins());
    stub.routes[`POST /bff/api/v1/guardians/${GUARDIAN}/optins`] = () =>
      problem(409, "opted_out_by_parent");
    renderWithIntl(
      <GuardianOptinsCard
        studentId={STUDENT}
        guardians={[{ id: GUARDIAN, full_name: "Synthetic Parent", version: 1 }]}
      />,
    );
    expect(await screen.findByText(en.optins.stoppedByParent)).toBeInTheDocument();
    expect(screen.getByText(en.optins.mobileEnding.replace("{last4}", "1234"))).toBeInTheDocument();
    expect(screen.getByText(en.optins.states.not_asked)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: en.optins.record }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: en.optins.save }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/guardians/${GUARDIAN}/optins`)).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/guardians/${GUARDIAN}/optins`)[0];
    expect(call?.headers.get("If-Match")).toBe('W/"2"');
    expect(JSON.parse(call?.body ?? "{}")).toMatchObject({
      category: "notices",
      status: "opted_in",
      method: "in_person",
    });
    expect(await screen.findByText(en.errors.opted_out_by_parent.title)).toBeInTheDocument();
  });

  it("is hidden where messaging is not offered", async () => {
    signedIn([RECORD, READ]);
    stub.routes[`GET /bff/api/v1/guardians/${GUARDIAN}/optins`] = () =>
      problem(404, "module_unavailable");
    const { container } = renderWithIntl(
      <GuardianOptinsCard
        studentId={STUDENT}
        guardians={[{ id: GUARDIAN, full_name: "Synthetic Parent", version: 1 }]}
      />,
    );
    await waitFor(() =>
      expect(stub.callsTo(`GET /bff/api/v1/guardians/${GUARDIAN}/optins`).length).toBeGreaterThan(
        0,
      ),
    );
    await waitFor(() => expect(container.textContent).not.toContain(en.optins.title));
  });
});

describe("delivery log (US-2208)", () => {
  it("lists messages with plain reasons and the last four digits only", async () => {
    signedIn([READ]);
    const body: MessagePage = {
      items: [
        {
          id: "0192f3a4-0000-7000-8000-00000000f201",
          purpose: "absence_alert",
          kind: "template",
          template_key: "absence_alert",
          template_version: 1,
          language: "en",
          language_fallback: true,
          guardian_id: GUARDIAN,
          student_id: STUDENT,
          source_type: "attendance_day",
          source_id: "0192f3a4-0000-7000-8000-00000000f301",
          phone_last4: "1234",
          status: "failed",
          skip_reason: null,
          error_code: "meta:131026",
          error_reason: "not_on_whatsapp",
          attempts: 1,
          queued_at: "2026-10-17T05:30:00Z",
          sent_at: null,
          delivered_at: null,
          read_at: null,
          failed_at: "2026-10-17T05:31:00Z",
        },
      ],
      next_cursor: null,
    };
    stub.routes["POST /bff/api/v1/messages/search"] = () => Response.json(body);
    renderWithIntl(<MessagesScreen guardianId={GUARDIAN} />);
    expect(await screen.findByText(en.log.reasons.not_on_whatsapp)).toBeInTheDocument();
    expect(screen.getByText(en.log.fallback)).toBeInTheDocument();
    expect(screen.getByText(en.log.phoneEnding.replace("{last4}", "1234"))).toBeInTheDocument();
    const call = stub.callsTo("POST /bff/api/v1/messages/search")[0];
    expect(call?.url.search).toBe("");
    expect(JSON.parse(call?.body ?? "{}")).toMatchObject({ guardian_id: GUARDIAN, limit: 50 });
  });

  it("filters by status in the POST body and shows the filter as a removable chip", async () => {
    signedIn([READ]);
    stub.routes["POST /bff/api/v1/messages/search"] = () =>
      Response.json({ items: [], next_cursor: null } satisfies MessagePage);
    renderWithIntl(<MessagesScreen />);
    expect(await screen.findByText(en.log.emptyTitle)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(en.log.status), "failed");
    expect(await screen.findByText(en.log.noResultsTitle)).toBeInTheDocument();
    const search = "POST /bff/api/v1/messages/search";
    await waitFor(() =>
      expect(JSON.parse(stub.callsTo(search).at(-1)?.body ?? "{}")).toMatchObject({
        status: "failed",
      }),
    );
    expect(stub.callsTo(search).every((c) => c.url.search === "")).toBe(true);
    await userEvent.click(
      screen.getByRole("button", {
        name: `Remove filter ${en.log.status}: ${en.log.statuses.failed}`,
      }),
    );
    expect(await screen.findByText(en.log.emptyTitle)).toBeInTheDocument();
  });
});

describe("WhatsApp set-up steps", () => {
  it("derive the step from the account's state only", () => {
    expect(connectStep({ connected: false, status: null })).toBe(0);
    expect(connectStep({ connected: false, status: "connecting" })).toBe(1);
    expect(connectStep({ connected: true, status: "active" })).toBe(2);
  });

  it("the settings form says when changes are not saved yet and can discard them", async () => {
    signedIn([CONFIGURE, READ]);
    stub.routes["GET /bff/api/v1/messaging/account"] = () => Response.json(connected());
    stub.routes["GET /bff/api/v1/messaging/settings"] = () => Response.json(settings());
    renderWithIntl(<WhatsAppSettingsPage />);
    const phone = await screen.findByLabelText(new RegExp(en.settings.officePhone));
    expect(screen.getByText(messages.en.ui.saveBar.noChanges)).toBeInTheDocument();
    await userEvent.type(phone, "9000011111");
    expect(screen.getByText(messages.en.ui.saveBar.unsaved)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: en.settings.discard }));
    expect(await screen.findByLabelText(new RegExp(en.settings.officePhone))).toHaveValue("");
    expect(stub.callsTo("PUT /bff/api/v1/messaging/settings")).toHaveLength(0);
  });
});
