import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { me } from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { InspectionScreen } from "./InspectionScreen";
import { indexHref, itemHref, readinessSchema, type PackItem } from "./packs-data";

/** Inspection readiness packs (FC-07, FO-12; FR-COMP-024..FR-COMP-045). Synthetic data only. */

const pm = messages.en.compliance.packs;
const PACK_ID = "0192f3a4-0000-7000-8000-0000000fc701";

function item(extra: Partial<PackItem> & Pick<PackItem, "key" | "source" | "state">): PackItem {
  return { label: extra.key, fix_by: "Office admin", ...extra };
}

const READINESS = {
  verified: false,
  source: "secondary sources",
  version: 1,
  packs: [
    {
      key: "deo_inspection",
      label: "DEO or MEO inspection",
      authority: "District or Mandal Educational Officer",
      note: "Unverified list.",
      counts: { ready: 1, attention: 0, missing: 1, not_tracked: 1, left_out: 0 },
      items: [
        item({
          key: "fire_noc",
          label: "Fire safety certificate",
          source: "certificate",
          state: "expired",
          valid_until: "2026-11-01",
          has_copy: true,
        }),
        item({
          key: "admission_register",
          label: "Admission and withdrawal register",
          source: "register",
          state: "ready",
          register_view: "admission_withdrawal",
        }),
        item({
          key: "committees",
          label: "School committees",
          source: "not_tracked",
          state: "not_tracked",
          feature: "FC-05",
        }),
      ],
    },
  ],
};

const MADE = {
  id: PACK_ID,
  pack_key: "deo_inspection",
  pack_label: "DEO or MEO inspection",
  authority: "District or Mandal Educational Officer",
  pack_version: 1,
  verified: false,
  academic_year_id: null,
  academic_year_label: "2026-27",
  visit_on: null,
  registers: ["admission_register"],
  counts: { ready: 1, attention: 0, missing: 1, not_tracked: 1, left_out: 0 },
  generated_at: "2026-11-12T05:00:00Z",
  generated_by_name: "Office",
  items: READINESS.packs[0]?.items ?? [],
};

let stub: BffStub;
beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/compliance/inspection-readiness"] = () => Response.json(READINESS);
  stub.routes["GET /bff/api/v1/compliance/inspection-packs"] = () => Response.json([]);
  stub.routes["GET /bff/api/v1/academic-years"] = () =>
    page([{ id: "y1", label: "2026-27", is_current: true }]);
});
afterEach(uninstallBffStub);

describe("inspection packs (FC-07, FO-12)", () => {
  it("parses the readiness answer and maps each source to its screen", () => {
    expect(readinessSchema.parse(READINESS).packs[0]?.items).toHaveLength(3);
    const [cert, register, untracked] = READINESS.packs[0]?.items ?? [];
    expect(cert && itemHref(cert)).toBe("/compliance");
    expect(register && itemHref(register)).toBe("/registers");
    expect(untracked && itemHref(untracked)).toBeNull();
    expect(indexHref(PACK_ID)).toBe(`/bff/api/v1/compliance/inspection-packs/${PACK_ID}/index`);
  });

  it("shows each item's state, 'not tracked in SchoolOS', and the unverified note (reader)", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["compliance.read"]));
    renderWithIntl(<InspectionScreen />);
    expect(await screen.findByText(pm.unverifiedTitle)).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "DEO or MEO inspection" });
    expect(within(table).getByText(pm.state.expired)).toBeInTheDocument();
    expect(within(table).getAllByText(pm.state.not_tracked).length).toBeGreaterThan(0);
    // A reader cannot make a pack.
    expect(screen.queryByRole("button", { name: pm.make.submit })).not.toBeInTheDocument();
  });

  it("makes a pack with the chosen register and an Idempotency-Key, then offers the index", async () => {
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(me(["compliance.read", "compliance.manage", "register.read"]));
    stub.routes["POST /bff/api/v1/compliance/inspection-packs"] = () =>
      Response.json(MADE, { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<InspectionScreen />);
    await user.click(await screen.findByRole("checkbox", { name: /Admission and withdrawal/ }));
    await user.click(screen.getByRole("button", { name: pm.make.submit }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/compliance/inspection-packs")).toHaveLength(1),
    );
    const [call] = stub.callsTo("POST /bff/api/v1/compliance/inspection-packs");
    expect(call?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      pack_key: "deo_inspection",
      registers: ["admission_register"],
    });
    expect(
      await screen.findByRole("link", { name: new RegExp(pm.make.printIndex) }),
    ).toHaveAttribute("href", indexHref(PACK_ID));
  });

  it("without register.read the registers are not offered", async () => {
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(me(["compliance.read", "compliance.manage"]));
    renderWithIntl(<InspectionScreen />);
    expect(await screen.findByText(pm.make.registersNotAllowed)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("explains a missing permission", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["student.create"]));
    renderWithIntl(<InspectionScreen />);
    expect(await screen.findByText(messages.en.compliance.noPermissionTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/compliance/inspection-readiness")).toHaveLength(0);
  });
});
