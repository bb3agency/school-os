import type { TenantProfile } from "@schoolos/api-client";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { BoardsCard, boardsPatch } from "./BoardsCard";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/settings/school",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/** Boards per school and class, and the alongside mode (US-203, US-204; FR-TEN-020..022). */

const en = messages.en.schoolSettings.boards;
const KEY = ["staff", "tenant"] as const;

const TENANT: TenantProfile = {
  id: "0192f3a4-0000-7000-8000-000000000001",
  code: "synth-a",
  name: "Synthetic Model School A",
  boards: ["CBSE"],
  state_code: "37",
  status: "active",
  plan_tier: "shared",
  deployment_mode: "shared",
  settings: {
    languages: ["en"],
    date_format: "DD/MM/YYYY",
    idle_timeout_minutes: 15,
    ai_features_enabled: true,
    ai_memory_enabled: true,
    ai_monthly_budget_inr: 5000,
    class_boards: {},
    operating_mode: "full",
    current_erp_name: "",
  },
  modules_hidden: [],
  version: 7,
};

let stub: BffStub;
beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => uninstallBffStub());

describe("FR-TEN-020..022: boards and operating mode", () => {
  it("sends only what changed", () => {
    expect(boardsPatch(TENANT, ["CBSE"], {}, "full", "")).toEqual({});
    expect(
      boardsPatch(TENANT, ["CBSE", "BSEAP"], { XI: "BSEAP" }, "alongside", "Synthetic ERP"),
    ).toEqual({
      boards: ["CBSE", "BSEAP"],
      class_boards: { XI: "BSEAP" },
      operating_mode: "alongside",
      current_erp_name: "Synthetic ERP",
    });
  });

  it("asks for class boards with two boards and the ERP name in alongside mode, then saves", async () => {
    stub.routes["PATCH /bff/api/v1/tenant"] = () => Response.json({ ...TENANT, version: 8 });
    const user = userEvent.setup();
    renderWithIntl(<BoardsCard tenant={TENANT} manage tenantKey={KEY} />);
    expect(screen.queryByLabelText(en.classLabel.replace("{code}", "XI"))).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: en.names.BSEAP }));
    const classXi = screen.getByLabelText(en.classLabel.replace("{code}", "XI"));
    await user.selectOptions(classXi, "BSEAP");
    expect(screen.queryByLabelText(en.erpNameField)).toBeNull();
    await user.click(screen.getByRole("radio", { name: en.modes.alongside.label }));
    await user.type(screen.getByLabelText(en.erpNameField), "Synthetic ERP");
    await user.click(screen.getByRole("button", { name: en.save }));
    await waitFor(() => expect(stub.callsTo("PATCH /bff/api/v1/tenant")).toHaveLength(1));
    const call = stub.callsTo("PATCH /bff/api/v1/tenant")[0];
    expect(call?.headers.get("if-match")).toBe('W/"7"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      boards: ["BSEAP", "CBSE"],
      class_boards: { XI: "BSEAP" },
      operating_mode: "alongside",
      current_erp_name: "Synthetic ERP",
    });
    expect(await screen.findByText(en.saved)).toBeInTheDocument();
  });

  it("says nothing changed without calling the API", async () => {
    const user = userEvent.setup();
    renderWithIntl(<BoardsCard tenant={TENANT} manage tenantKey={KEY} />);
    await user.click(screen.getByRole("button", { name: en.save }));
    expect(await screen.findByText(en.unchanged)).toBeInTheDocument();
    expect(stub.callsTo("PATCH /bff/api/v1/tenant")).toHaveLength(0);
  });

  it("shows the choice read-only without tenant.settings.manage", () => {
    renderWithIntl(
      <BoardsCard
        tenant={{
          ...TENANT,
          boards: ["CBSE", "BSEAP"],
          settings: {
            ...TENANT.settings,
            class_boards: { XI: "BSEAP" },
            operating_mode: "alongside",
          },
        }}
        manage={false}
        tenantKey={KEY}
      />,
    );
    expect(screen.queryByRole("button", { name: en.save })).toBeNull();
    expect(screen.getByText(`${en.names.CBSE}, ${en.names.BSEAP}`)).toBeInTheDocument();
    expect(screen.getByText(en.modes.alongside.label)).toBeInTheDocument();
  });
});
