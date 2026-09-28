import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SchoolSettingsPage from "@/app/[locale]/(school)/settings/school/page";
import { SESSION_INFO_EVENT } from "@/lib/bff/session-client";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { changedSettings, settingsSchema } from "./data";
import { SchoolSettingsScreen } from "./SchoolSettingsScreen";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/settings/school",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const MANAGE = "tenant.settings.manage";
const en = messages.en.schoolSettings;

// Synthetic school in the generated API shape (TenantOut).
const SETTINGS = {
  languages: ["en", "te"] as ("en" | "te")[],
  date_format: "DD/MM/YYYY" as const,
  idle_timeout_minutes: 15,
  ai_features_enabled: true,
  ai_monthly_budget_inr: 5000,
};
const TENANT = {
  id: "0192f3a4-0000-7000-8000-000000000001",
  code: "synth-a",
  name: "Synthetic Model School A",
  boards: ["STATE_AP", "CBSE"],
  state_code: "37",
  status: "active" as const,
  plan_tier: "shared" as const,
  deployment_mode: "shared" as const,
  settings: SETTINGS,
  version: 7,
};

let stub: BffStub;
let tenant = TENANT;

function school(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
  stub.routes["GET /bff/api/v1/tenant"] = () => Response.json(tenant);
}

beforeEach(() => {
  stub = installBffStub("staff");
  tenant = TENANT;
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("school settings (FR-TEN-012)", () => {
  it("shows the school profile and settings read-only without tenant.settings.manage", async () => {
    school(["student.read_basic"]);
    renderWithIntl(<SchoolSettingsScreen />);
    expect(await screen.findByText("Synthetic Model School A")).toBeInTheDocument();
    expect(screen.getByText("synth-a")).toBeInTheDocument();
    expect(screen.getByText("STATE_AP, CBSE")).toBeInTheDocument();
    expect(screen.getByText("15 minutes")).toBeInTheDocument();
    expect(await screen.findByText(en.readOnlyNote)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.form.save })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("the page renders under its own title", async () => {
    school([]);
    renderWithIntl(<SchoolSettingsPage />);
    expect(await screen.findByRole("heading", { level: 1, name: en.title })).toBeInTheDocument();
  });

  it("saves only the changed settings with If-Match and says step-up may be asked", async () => {
    school([MANAGE]);
    stub.routes["PATCH /bff/api/v1/tenant"] = () =>
      Response.json({ ...TENANT, settings: { ...SETTINGS, idle_timeout_minutes: 20 }, version: 8 });
    renderWithIntl(<SchoolSettingsScreen />);
    const idle = await screen.findByLabelText(en.form.idleField);
    expect(idle).toHaveValue("15");
    expect(screen.getByText(messages.en.common.stepUpNote)).toBeInTheDocument();
    await userEvent.clear(idle);
    await userEvent.type(idle, "20");
    await userEvent.click(screen.getByRole("radio", { name: "YYYY-MM-DD" }));
    await userEvent.click(screen.getByRole("button", { name: en.form.save }));
    await waitFor(() => expect(stub.callsTo("PATCH /bff/api/v1/tenant")).toHaveLength(1));
    const call = stub.callsTo("PATCH /bff/api/v1/tenant")[0];
    expect(call?.headers.get("If-Match")).toBe('W/"7"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      idle_timeout_minutes: 20,
      date_format: "YYYY-MM-DD",
    });
    expect(await screen.findByText(en.form.saved)).toBeInTheDocument();
  });

  it("after saving, re-reads /me and the session info so a new idle timeout applies at once", async () => {
    school([MANAGE]);
    stub.routes["PATCH /bff/api/v1/tenant"] = () =>
      Response.json({ ...TENANT, settings: { ...SETTINGS, idle_timeout_minutes: 5 }, version: 8 });
    const seen: string[] = [];
    const onInfo = (event: Event) => {
      const detail = (event as CustomEvent<{ kind: string }>).detail;
      seen.push(detail.kind);
    };
    window.addEventListener(SESSION_INFO_EVENT, onInfo);
    try {
      renderWithIntl(<SchoolSettingsScreen />);
      const idle = await screen.findByLabelText(en.form.idleField);
      await waitFor(() => expect(stub.callsTo("GET /bff/api/v1/me")).toHaveLength(1));
      const sessionReads = stub.callsTo("GET /bff/auth/session").length;
      await userEvent.clear(idle);
      await userEvent.type(idle, "5");
      await userEvent.click(screen.getByRole("button", { name: en.form.save }));
      expect(await screen.findByText(en.form.saved)).toBeInTheDocument();
      // /me goes through the BFF, which applies the school's timeout to the session...
      await waitFor(() => expect(stub.callsTo("GET /bff/api/v1/me")).toHaveLength(2));
      // ...then the session facts are read again (not from the page's cache) for the warning.
      await waitFor(() => expect(seen).toEqual(["staff"]));
      const me = stub.callsTo("GET /bff/api/v1/me").at(-1);
      const info = stub.callsTo("GET /bff/auth/session").at(-1);
      expect(stub.callsTo("GET /bff/auth/session").length).toBeGreaterThan(sessionReads);
      expect(stub.calls.indexOf(info!)).toBeGreaterThan(stub.calls.indexOf(me!));
    } finally {
      window.removeEventListener(SESSION_INFO_EVENT, onInfo);
    }
  });

  it("sends nothing when nothing changed, and checks values before sending", async () => {
    school([MANAGE]);
    renderWithIntl(<SchoolSettingsScreen />);
    await userEvent.click(await screen.findByRole("button", { name: en.form.save }));
    expect(await screen.findByText(en.form.nothingChanged)).toBeInTheDocument();

    const idle = screen.getByLabelText(en.form.idleField);
    await userEvent.clear(idle);
    await userEvent.type(idle, "3");
    await userEvent.click(screen.getByRole("checkbox", { name: "English" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "తెలుగు" }));
    await userEvent.click(screen.getByRole("button", { name: en.form.save }));
    expect(await screen.findByText(messages.en.validation.invalidNumber)).toBeInTheDocument();
    expect(screen.getByText(messages.en.validation.chooseOption)).toBeInTheDocument();
    expect(idle).toHaveAttribute("aria-invalid", "true");
    expect(stub.callsTo("PATCH /bff/api/v1/tenant")).toHaveLength(0);
  });

  it("on 412 says someone else changed the settings and reloads the latest on request", async () => {
    school([MANAGE]);
    stub.routes["PATCH /bff/api/v1/tenant"] = () => problem(412, "precondition_failed");
    renderWithIntl(<SchoolSettingsScreen />);
    const budget = await screen.findByLabelText(en.form.budgetField);
    await userEvent.clear(budget);
    await userEvent.type(budget, "8000");
    await userEvent.click(screen.getByRole("button", { name: en.form.save }));
    expect(await screen.findByText(en.errors.precondition_failed.title)).toBeInTheDocument();
    tenant = { ...TENANT, settings: { ...SETTINGS, ai_monthly_budget_inr: 6000 }, version: 8 };
    await userEvent.click(screen.getByRole("button", { name: en.form.reload }));
    await waitFor(() => expect(screen.getByLabelText(en.form.budgetField)).toHaveValue("6000"));
    expect(screen.queryByText(en.errors.precondition_failed.title)).toBeNull();
  });

  it("maps a server 422 on a field to that field", async () => {
    school([MANAGE]);
    stub.routes["PATCH /bff/api/v1/tenant"] = () =>
      problem(422, "validation_error", {
        errors: [
          { field: "ai_monthly_budget_inr", code: "invalid", message_key: "errors.invalid" },
        ],
      });
    renderWithIntl(<SchoolSettingsScreen />);
    const budget = await screen.findByLabelText(en.form.budgetField);
    await userEvent.clear(budget);
    await userEvent.type(budget, "9000");
    await userEvent.click(screen.getByRole("button", { name: en.form.save }));
    await waitFor(() => expect(budget).toHaveAttribute("aria-invalid", "true"));
  });

  it("computes the change set and validates like the API", () => {
    const parsed = settingsSchema.parse({
      languages: ["te", "en"],
      date_format: "DD/MM/YYYY",
      idle_timeout_minutes: "15",
      ai_features_enabled: "on",
      ai_monthly_budget_inr: "5000",
    });
    expect(changedSettings(SETTINGS, parsed)).toEqual({});
    expect(
      changedSettings(SETTINGS, { ...parsed, languages: ["te"], ai_features_enabled: false }),
    ).toEqual({ languages: ["te"], ai_features_enabled: false });
    expect(settingsSchema.safeParse({ ...parsed, idle_timeout_minutes: "31" }).success).toBe(false);
    expect(
      settingsSchema
        .safeParse({
          languages: ["en"],
          date_format: "MM/DD/YYYY",
          idle_timeout_minutes: "15",
          ai_monthly_budget_inr: "10000001",
        })
        .error?.issues.map((issue) => issue.path[0]),
    ).toEqual(["date_format", "ai_monthly_budget_inr"]);
  });

  it("renders in Telugu with no missing messages", async () => {
    school([MANAGE]);
    renderWithIntl(<SchoolSettingsScreen />, "te");
    expect(
      await screen.findByRole("heading", { level: 1, name: messages.te.schoolSettings.title }),
    ).toBeInTheDocument();
    expect(
      await screen.findByLabelText(messages.te.schoolSettings.form.idleField),
    ).toBeInTheDocument();
    const profile = screen.getByRole("region", { name: messages.te.schoolSettings.profile.title });
    expect(within(profile).getByText("Synthetic Model School A")).toBeInTheDocument();
  });
});
