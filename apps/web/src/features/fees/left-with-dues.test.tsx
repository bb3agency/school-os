import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import type { LeftWithDues } from "./data";
import { LeftWithDuesScreen } from "./LeftWithDues";

/**
 * FM-01 "Left with dues" (FR-FEE-038, FR-FEE-040, FR-FEE-042): finance.read sees the list and
 * the note that it is never on a TC and never blocks one; fee.report downloads the CSV (step-up
 * like other fee downloads). Synthetic names and amounts only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/fees/left-with-dues",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.fees.leftWithDues;
const REPORT: LeftWithDues = {
  as_of: "2026-11-09",
  students: 2,
  total_balance: "9300.00",
  written_off_total: "1500.00",
  rows: [
    {
      student_id: "0192f3a4-0000-7000-8000-00000000e501",
      display_name: "Synthetica Left Early",
      admission_no: "F-0501",
      status: "left",
      accounts: 2,
      balance: "6300.00",
      oldest_due_on: "2025-07-15",
      last_payment_on: "2026-02-10",
      written_off: "1500.00",
    },
    {
      student_id: "0192f3a4-0000-7000-8000-00000000e502",
      display_name: null,
      admission_no: "F-0502",
      status: "graduated",
      accounts: 1,
      balance: "3000.00",
      oldest_due_on: "2026-01-15",
      last_payment_on: null,
      written_off: "0.00",
    },
  ],
};

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/fees/reports/left-with-dues"] = () => Response.json(REPORT);
  stub.routes["GET /bff/api/v1/fees/reports/left-with-dues.csv"] = () =>
    new Response("student,balance\n", { headers: { "content-type": "text/csv" } });
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["principal"] }));
}

describe("Left with dues (FR-FEE-038)", () => {
  it("lists students who left with fees open and says it never touches a TC", async () => {
    signedIn(["finance.read"]);
    renderWithIntl(<LeftWithDuesScreen />);
    expect(await screen.findByText("Synthetica Left Early")).toBeInTheDocument();
    expect(screen.getByText(en.noName)).toBeInTheDocument();
    expect(screen.getAllByText(en.status.left).length).toBeGreaterThan(0);
    expect(screen.getAllByText(en.status.graduated).length).toBeGreaterThan(0);
    expect(screen.getByText(en.infoTitle)).toBeInTheDocument();
    expect(screen.getByText(en.infoBody)).toBeInTheDocument();
    expect(screen.getByText("₹9,300.00")).toBeInTheDocument();
    expect(screen.getAllByText("₹1,500.00").length).toBeGreaterThan(0);
    // No CSV without fee.report.
    expect(screen.queryByRole("button", { name: messages.en.ui.more })).toBeNull();
  });

  it("explains who can see it, without calling the API", async () => {
    signedIn(["fee.collect"]);
    renderWithIntl(<LeftWithDuesScreen />);
    expect(await screen.findByText(messages.en.fees.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/fees/reports/left-with-dues")).toHaveLength(0);
  });

  it("says fees are off (404 module_hidden)", async () => {
    signedIn(["finance.read"]);
    stub.routes["GET /bff/api/v1/fees/reports/left-with-dues"] = () =>
      problem(404, "module_hidden");
    renderWithIntl(<LeftWithDuesScreen />);
    expect(await screen.findByText(messages.en.fees.off.title)).toBeInTheDocument();
  });

  it("says when nobody left with dues", async () => {
    signedIn(["finance.read"]);
    stub.routes["GET /bff/api/v1/fees/reports/left-with-dues"] = () =>
      Response.json({ ...REPORT, students: 0, total_balance: "0.00", rows: [] });
    renderWithIntl(<LeftWithDuesScreen />);
    expect(await screen.findByText(en.emptyTitle)).toBeInTheDocument();
  });

  it("downloads the CSV through the API client with fee.report", async () => {
    signedIn(["finance.read", "fee.report"]);
    const createObjectURL = vi.fn(() => "blob:csv");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const user = userEvent.setup();
    renderWithIntl(<LeftWithDuesScreen />);
    await screen.findByText("Synthetica Left Early");
    await user.click(screen.getByRole("button", { name: messages.en.ui.more }));
    await user.click(await screen.findByRole("menuitem", { name: en.csv }));
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/fees/reports/left-with-dues.csv")).toHaveLength(1),
    );
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
  });

  it("shows the step-up prompt when the download needs a fresh sign-in (428)", async () => {
    signedIn(["finance.read", "fee.report"]);
    stub.routes["GET /bff/api/v1/fees/reports/left-with-dues.csv"] = () =>
      problem(428, "step_up_required", { step_up_url: "/bff/auth/step-up" });
    const user = userEvent.setup();
    renderWithIntl(<LeftWithDuesScreen />);
    await screen.findByText("Synthetica Left Early");
    await user.click(screen.getByRole("button", { name: messages.en.ui.more }));
    await user.click(await screen.findByRole("menuitem", { name: en.csv }));
    await waitFor(() => expect(stub.navigate).toHaveBeenCalled());
  });
});
