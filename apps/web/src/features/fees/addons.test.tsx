import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { amountsOf, type AddonList, type AddonStudents } from "./addons";
import { AddonsScreen } from "./AddonsScreen";

/**
 * FM-02 add-on fees (FR-FEE-081..100; D85): a transport in-charge (`fee.addon.read`) sees who is
 * on each add-on without amounts; the accountant (`fee.manage`) sets up templates and puts a
 * section on one; taking students off needs a reason. Synthetic names and amounts only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/fees/addons",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.fees.addons;
const ADDON = {
  id: "0192f3a4-0000-7000-8000-00000000a001",
  academic_year_id: "0192f3a4-0000-7000-8000-00000000a0y1",
  head_id: "0192f3a4-0000-7000-8000-00000000a0h1",
  head_code: "VAN",
  head_name: "Transport fee",
  head_kind: "transport",
  label: "Band 2 (5-10 km)",
  active: true,
  assigned: 1,
  amounts: { "1": "1500.00", "2": "1500.00" },
  total: "3000.00",
  version: 1,
};
const LIST: AddonList = { items: [ADDON], amounts_shown: true, gst_note: "Synthetic GST note." };
const STUDENTS: AddonStudents = {
  addon_id: ADDON.id,
  label: ADDON.label,
  head_name: "Transport fee",
  amounts_shown: true,
  items: [
    {
      assignment_id: "0192f3a4-0000-7000-8000-00000000a0a1",
      student_id: "0192f3a4-0000-7000-8000-00000000a0s1",
      display_name: "Synthetica Bus Rider",
      admission_no: "F-0901",
      class_section: "IX-A",
      start_instalment: 1,
      end_instalment: null,
      status: "active",
      charged: "3000.00",
    },
  ],
};
const BASE = "/bff/api/v1/fees/addons";

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes[`GET ${BASE}`] = () => Response.json(LIST);
  stub.routes[`GET ${BASE}/${ADDON.id}/students`] = () => Response.json(STUDENTS);
  stub.routes["GET /bff/api/v1/fees/heads"] = () =>
    Response.json([
      {
        id: ADDON.head_id,
        code: "VAN",
        name: "Transport fee",
        kind: "transport",
        gst_exempt: true,
        active: true,
        sort_order: 1,
        tax_tuition: false,
        version: 1,
      },
    ]);
  stub.routes["GET /bff/api/v1/academic-years"] = () =>
    page([
      {
        id: ADDON.academic_year_id,
        label: "2026-27",
        starts_on: "2026-06-01",
        ends_on: "2027-03-31",
        is_current: true,
      },
    ]);
  stub.routes["GET /bff/api/v1/classes"] = () =>
    page([{ id: "c9", display_en: "Class IX", display_te: "IX", sort_order: 9 }]);
  stub.routes["GET /bff/api/v1/sections"] = () =>
    page([{ id: "s9a", class_id: "c9", academic_year_id: ADDON.academic_year_id, name: "A" }]);
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["accountant"] }));
}

describe("Add-on fees (FR-FEE-095, FR-FEE-100)", () => {
  it("shows the in-charge who is on an add-on without amounts", async () => {
    signedIn(["fee.addon.read", "student.read_basic"]);
    stub.routes[`GET ${BASE}`] = () =>
      Response.json({
        ...LIST,
        amounts_shown: false,
        items: [{ ...ADDON, amounts: null, total: null }],
      });
    stub.routes[`GET ${BASE}/${ADDON.id}/students`] = () =>
      Response.json({
        ...STUDENTS,
        amounts_shown: false,
        items: STUDENTS.items.map((row) => ({ ...row, charged: null })),
      });
    const user = userEvent.setup();
    renderWithIntl(<AddonsScreen />);
    await user.click(await screen.findByRole("button", { name: `Students on ${ADDON.label}` }));
    expect(await screen.findByText("Synthetica Bus Rider")).toBeInTheDocument();
    expect(screen.queryByText("₹3,000.00")).toBeNull();
    expect(screen.queryByText(en.columns.total)).toBeNull();
    // No set-up or assignment controls for a reader.
    expect(screen.queryByText(en.create.title)).toBeNull();
    expect(screen.queryByText(en.assign.title)).toBeNull();
  });

  it("explains who can see it, without calling the API", async () => {
    signedIn(["fee.collect"]);
    renderWithIntl(<AddonsScreen />);
    expect(await screen.findByText(messages.en.fees.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo(`GET ${BASE}`)).toHaveLength(0);
  });

  it("says fees are off (404 module_hidden)", async () => {
    signedIn(["fee.manage", "finance.read"]);
    stub.routes[`GET ${BASE}`] = () => problem(404, "module_hidden");
    renderWithIntl(<AddonsScreen />);
    expect(await screen.findByText(messages.en.fees.off.title)).toBeInTheDocument();
  });

  it("lets the accountant create an add-on with amounts per instalment", async () => {
    signedIn(["fee.manage", "finance.read"]);
    stub.routes[`POST ${BASE}`] = () => Response.json({ ...ADDON, id: "new" }, { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<AddonsScreen />);
    expect(await screen.findByText("Synthetic GST note.")).toBeInTheDocument();
    expect(screen.getByText("₹3,000.00")).toBeInTheDocument();
    const form = screen.getByRole("region", { name: en.create.title });
    await user.selectOptions(
      await within(form).findByRole("combobox", { name: new RegExp(en.create.head) }),
      ADDON.head_id,
    );
    await user.type(
      within(form).getByRole("textbox", { name: new RegExp(`^${en.create.label}`) }),
      "Hostel",
    );
    await user.type(within(form).getByRole("textbox", { name: "Instalment 1" }), "9000");
    await user.click(within(form).getByRole("button", { name: en.create.submit }));
    await waitFor(() => expect(stub.callsTo(`POST ${BASE}`)).toHaveLength(1));
    const call = stub.callsTo(`POST ${BASE}`)[0]!;
    expect(JSON.parse(call.body)).toEqual({
      academic_year_id: ADDON.academic_year_id,
      head_id: ADDON.head_id,
      label: "Hostel",
      amounts: { "1": "9000.00" },
    });
    expect(call.headers.get("Idempotency-Key")).toBeTruthy();
  });

  it("puts a section on an add-on from an instalment", async () => {
    signedIn(["fee.manage", "finance.read"]);
    stub.routes[`POST ${BASE}/${ADDON.id}/assign`] = () =>
      Response.json({
        dry_run: false,
        students: 30,
        assigned: 29,
        charges_posted: 29,
        replaced: 0,
        credits_proposed: 0,
        skipped: [{ student_id: "x", reason: "already_assigned" }],
      });
    const user = userEvent.setup();
    renderWithIntl(<AddonsScreen />);
    await user.click(await screen.findByRole("button", { name: `Students on ${ADDON.label}` }));
    expect(await screen.findByText("Synthetica Bus Rider")).toBeInTheDocument();
    await user.selectOptions(
      await screen.findByRole("combobox", { name: en.assign.modes.section }),
      "s9a",
    );
    const start = screen.getByRole("spinbutton", { name: new RegExp(en.assign.startInstalment) });
    await user.clear(start);
    await user.type(start, "2");
    await user.click(screen.getByRole("button", { name: en.assign.submitAssign }));
    await waitFor(() => expect(stub.callsTo(`POST ${BASE}/${ADDON.id}/assign`)).toHaveLength(1));
    expect(JSON.parse(stub.callsTo(`POST ${BASE}/${ADDON.id}/assign`)[0]!.body)).toEqual({
      section_ids: ["s9a"],
      start_instalment: 2,
      dry_run: false,
    });
    expect(await screen.findByText(en.result.done)).toBeInTheDocument();
  });

  it("needs a reason before taking students off", async () => {
    signedIn(["fee.manage", "finance.read"]);
    const user = userEvent.setup();
    renderWithIntl(<AddonsScreen />);
    await user.click(await screen.findByRole("button", { name: `Students on ${ADDON.label}` }));
    await user.selectOptions(
      await screen.findByRole("combobox", { name: en.assign.action }),
      "unassign",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: en.assign.modes.section }),
      "s9a",
    );
    expect(screen.getByRole("button", { name: en.assign.submitUnassign })).toBeDisabled();
    await user.type(
      screen.getByRole("textbox", { name: new RegExp(en.assign.reason) }),
      "Moved closer to school",
    );
    expect(screen.getByRole("button", { name: en.assign.submitUnassign })).toBeEnabled();
  });
});

describe("amountsOf", () => {
  it("turns typed rupees into exact strings and refuses bad ones", () => {
    expect(amountsOf(["1500", "", "99.5"])).toEqual({ "1": "1500.00", "3": "99.50" });
    expect(amountsOf(["", ""])).toBeNull();
    expect(amountsOf(["0"])).toBeNull();
    expect(amountsOf(["12.345"])).toBeNull();
    expect(amountsOf(["-5"])).toBeNull();
  });
});
