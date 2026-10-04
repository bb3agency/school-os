import type { Announcement } from "@schoolos/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { utcToLocalDateTime } from "./AnnouncementEditor";
import { AnnouncementsScreen } from "./AnnouncementsView";

/**
 * Editing an announcement (FR-PLT-026, docs/16 §5.13): PATCH with If-Match, the same fields
 * and limits as a new one, hidden without `platform.announcements.manage` and on cancelled
 * announcements, plain-language errors. Synthetic data only.
 */

const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const T1 = "0192f3a4-0000-7000-8000-000000000001";
const T2 = "0192f3a4-0000-7000-8000-000000000002";
const A1 = "0192f3a4-0000-7000-8000-00000000c101";
const A2 = "0192f3a4-0000-7000-8000-00000000c102";
const a = messages.en.platform.announcements;
const api = messages.en.errors.api;
const PATCH_A1 = `PATCH /bff/api/v1/platform/announcements/${A1}`;

function announcement(overrides: Partial<Announcement> = {}): Announcement {
  return {
    id: A1,
    title_en: "Maintenance on Sunday",
    title_te: "Maintenance on Sunday",
    body_en: "SchoolOS is unavailable 06:00–07:00.",
    body_te: "SchoolOS is unavailable 06:00–07:00.",
    severity: "maintenance",
    audience: "tier",
    audience_tier: "dedicated",
    audience_tenant_ids: [],
    starts_at: "2026-10-11T00:30:00Z",
    ends_at: "2026-10-11T01:30:00Z",
    status: "scheduled",
    version: 4,
    ...overrides,
  };
}

function tenant(id: string, name: string, code: string) {
  return {
    tenant_id: id,
    school_name: name,
    code,
    tier: "shared",
    tenant_status: "active",
    subscription_status: "active",
    plan_code: "standard",
    deployment_status: "healthy",
    app_version: null,
    last_heartbeat_at: null,
    created_at: "2026-06-01T04:30:00Z",
  };
}

let stub: BffStub;

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/platform/me"] = () =>
    Response.json({ operator_id: OP, roles: ["support_agent"], permissions, step_up_fresh: true });
}

function listing(rows: Announcement[]) {
  stub.routes["GET /bff/api/v1/platform/announcements"] = () => page(rows);
}

beforeEach(() => {
  stub = installBffStub("operator");
  signedIn(["platform.tenants.read", "platform.announcements.manage"]);
  stub.routes["GET /bff/api/v1/platform/tenants"] = () =>
    page([
      tenant(T1, "Sri Saraswati High School", "sshs"),
      tenant(T2, "Vidya Niketan School", "vns"),
    ]);
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

async function openEdit(title = announcement().title_en) {
  const user = userEvent.setup();
  const edit = await screen.findByRole("button", { name: a.edit, description: title });
  await user.click(edit);
  const dialog = await screen.findByRole("dialog", { name: a.editTitle });
  return { user, edit, dialog };
}

function bodyOf(key: string) {
  return JSON.parse(stub.callsTo(key)[0]?.body ?? "{}") as Record<string, unknown>;
}

describe("edit an announcement (FR-PLT-026)", () => {
  it("opens with the stored values in IST, sends the banner with If-Match and returns focus", async () => {
    listing([announcement()]);
    stub.routes[PATCH_A1] = () => Response.json(announcement({ version: 5 }));
    renderWithIntl(<AnnouncementsScreen />);
    const { user, edit, dialog } = await openEdit();
    const d = within(dialog);
    expect(d.getByLabelText(a.titleLabel)).toHaveValue("Maintenance on Sunday");
    expect(d.getByLabelText(a.audience)).toHaveValue("dedicated");
    expect(d.getByLabelText(a.severity)).toHaveValue("maintenance");
    expect(d.getByLabelText(a.publishAs)).toHaveValue("scheduled");
    expect(d.getByLabelText(a.startsAt)).toHaveValue("2026-10-11T06:00");
    expect(d.getByLabelText(a.endsAt)).toHaveValue("2026-10-11T07:00");
    // Telugu is switched off (ADR-0036): no Telugu fields in the dialog.
    expect(d.queryByRole("tab", { name: a.telugu })).toBeNull();

    await user.clear(d.getByLabelText(a.titleLabel));
    await user.type(d.getByLabelText(a.titleLabel), "Maintenance moved to Monday");
    await user.clear(d.getByLabelText(a.startsAt));
    await user.type(d.getByLabelText(a.startsAt), "2026-10-12T06:00");
    await user.clear(d.getByLabelText(a.endsAt));
    await user.type(d.getByLabelText(a.endsAt), "2026-10-12T07:00");
    await user.click(d.getByRole("button", { name: a.editSave }));

    await waitFor(() => expect(stub.callsTo(PATCH_A1)).toHaveLength(1));
    expect(stub.callsTo(PATCH_A1)[0]?.headers.get("If-Match")).toBe('"4"');
    // The API replaces the banner (AnnouncementIn): every field is sent. The Telugu texts go
    // empty while Telugu is hidden, so the API keeps the ones it has.
    expect(bodyOf(PATCH_A1)).toEqual({
      title_en: "Maintenance moved to Monday",
      title_te: "",
      body_en: "SchoolOS is unavailable 06:00–07:00.",
      body_te: "",
      severity: "maintenance",
      audience: "tier",
      audience_tier: "dedicated",
      audience_tenant_ids: [],
      starts_at: "2026-10-12T00:30:00.000Z",
      ends_at: "2026-10-12T01:30:00.000Z",
      status: "scheduled",
    });
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    expect(edit).toHaveFocus();
    expect(await screen.findByText(a.updated)).toBeInTheDocument();
  });

  it("keeps the chosen schools and the Telugu texts while Telugu is switched on", async () => {
    listing([
      announcement({
        audience: "tenants",
        audience_tier: null,
        audience_tenant_ids: [T2],
        title_te: "ఆదివారం నిర్వహణ",
        body_te: "06:00–07:00 వరకు SchoolOS అందుబాటులో ఉండదు.",
        status: "draft",
      }),
    ]);
    stub.routes[PATCH_A1] = () => Response.json(announcement());
    renderWithIntl(<AnnouncementsScreen />, { telugu: true });
    const { user, dialog } = await openEdit();
    const d = within(dialog);
    expect(d.getByLabelText(a.publishAs)).toHaveValue("draft");
    expect(d.getByRole("checkbox", { name: /Vidya Niketan School/ })).toBeChecked();
    expect(d.getByRole("checkbox", { name: /Sri Saraswati High School/ })).not.toBeChecked();
    await user.click(d.getByRole("checkbox", { name: /Sri Saraswati High School/ }));
    await user.click(d.getByRole("button", { name: a.editSave }));
    await waitFor(() => expect(stub.callsTo(PATCH_A1)).toHaveLength(1));
    expect(bodyOf(PATCH_A1)).toMatchObject({
      title_te: "ఆదివారం నిర్వహణ",
      body_te: "06:00–07:00 వరకు SchoolOS అందుబాటులో ఉండదు.",
      audience: "tenants",
      audience_tier: null,
      audience_tenant_ids: [T1, T2],
      status: "draft",
    });
  });

  it("validates like a new announcement before sending", async () => {
    listing([announcement()]);
    renderWithIntl(<AnnouncementsScreen />);
    const { user, dialog } = await openEdit();
    const d = within(dialog);
    await user.clear(d.getByLabelText(a.titleLabel));
    await user.clear(d.getByLabelText(a.endsAt));
    await user.type(d.getByLabelText(a.endsAt), "2026-10-11T05:00");
    await user.click(d.getByRole("button", { name: a.editSave }));
    expect(await d.findByText(messages.en.validation.required)).toBeInTheDocument();
    expect(d.getByText(messages.en.validation.endAfterStart)).toBeInTheDocument();
    expect(d.getByLabelText(a.titleLabel)).toHaveAttribute("maxLength", "120");
    expect(stub.callsTo(PATCH_A1)).toHaveLength(0);
  });

  it("someone else's change first (412) says so and refreshes the list", async () => {
    listing([announcement()]);
    stub.routes[PATCH_A1] = () => problem(412, "precondition_failed");
    renderWithIntl(<AnnouncementsScreen />);
    const { user, dialog } = await openEdit();
    const before = stub.callsTo("GET /bff/api/v1/platform/announcements").length;
    await user.click(within(dialog).getByRole("button", { name: a.editSave }));
    expect(await within(dialog).findByText(api.precondition_failed.title)).toBeInTheDocument();
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/platform/announcements").length).toBeGreaterThan(before),
    );
  });

  it("a cancellation meanwhile (409) and a refusal (403) are explained", async () => {
    listing([announcement()]);
    stub.routes[PATCH_A1] = () => problem(409, "invalid_state");
    renderWithIntl(<AnnouncementsScreen />);
    const { user, dialog } = await openEdit();
    await user.click(within(dialog).getByRole("button", { name: a.editSave }));
    expect(await within(dialog).findByText(api.invalid_state.title)).toBeInTheDocument();
    stub.routes[PATCH_A1] = () => problem(403, "forbidden");
    await user.click(within(dialog).getByRole("button", { name: a.editSave }));
    expect(await within(dialog).findByText(api.forbidden.title)).toBeInTheDocument();
  });

  it("a server field error (422) shows on the field", async () => {
    listing([announcement()]);
    stub.routes[PATCH_A1] = () =>
      problem(422, "validation_error", {
        errors: [
          { field: "body.title_en", code: "too_long", message_key: "errors.string_too_long" },
        ],
      });
    renderWithIntl(<AnnouncementsScreen />);
    const { user, dialog } = await openEdit();
    await user.click(within(dialog).getByRole("button", { name: a.editSave }));
    await waitFor(() =>
      expect(within(dialog).getByLabelText(a.titleLabel)).toHaveAttribute("aria-invalid", "true"),
    );
  });

  it("no Edit on a cancelled announcement, and none without the permission", async () => {
    listing([
      announcement(),
      announcement({ id: A2, title_en: "Old notice", status: "cancelled" }),
    ]);
    const first = renderWithIntl(<AnnouncementsScreen />);
    expect(
      await screen.findByRole("button", { name: a.edit, description: announcement().title_en }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: a.edit })).toHaveLength(1);
    first.unmount();

    signedIn(["platform.tenants.read"]);
    renderWithIntl(<AnnouncementsScreen />);
    expect(await screen.findByText("Old notice")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(a.newTitle)).toBeNull());
    expect(screen.queryByRole("button", { name: a.edit })).toBeNull();
  });

  it("utcToLocalDateTime shows a UTC time in IST for datetime-local", () => {
    expect(utcToLocalDateTime("2026-10-11T18:45:00Z")).toBe("2026-10-12T00:15");
    expect(utcToLocalDateTime("not a time")).toBe("");
  });
});
