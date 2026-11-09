import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DuesScreen } from "@/features/fees/ReportsScreens";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";

/**
 * FM-01 earlier-year dues on the dues list (FR-FEE-031, FR-FEE-032): a year column, an "earlier
 * years only" filter and the arrears tile.
 * Synthetic values only.
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
const CURRENT = "0192f3a4-0000-7000-8000-00000000f101";
const EARLIER = "0192f3a4-0000-7000-8000-00000000f102";
const STUDENT = "0192f3a4-0000-7000-8000-00000000e101";
const YEAR_NOW = "0192f3a4-0000-7000-8000-00000000c101";
const YEAR_OLD = "0192f3a4-0000-7000-8000-00000000c102";

let stub: BffStub;

function row(account: string, year: string, label: string, earlier: boolean, overdue: string) {
  return {
    account_id: account,
    student_id: STUDENT,
    display_name: "Synthetica Arrears",
    admission_no: "F-0101",
    class_section: "Class IX A",
    overdue,
    oldest_due_on: earlier ? "2025-07-15" : "2026-09-21",
    balance: overdue,
    last_payment_on: null,
    academic_year_id: year,
    academic_year_label: label,
    earlier_year: earlier,
  };
}

function dues(arrearsOnly: boolean) {
  const rows = [
    row(EARLIER, YEAR_OLD, "2025-26", true, "4800.00"),
    ...(arrearsOnly ? [] : [row(CURRENT, YEAR_NOW, "2026-27", false, "3500.00")]),
  ];
  return {
    as_of: "2026-11-09",
    students: 1,
    total_overdue: arrearsOnly ? "4800.00" : "8300.00",
    arrears_students: 1,
    arrears_total: "4800.00",
    rows,
  };
}

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(["finance.read", "fee.report"], { roles: ["principal"] }));
  stub.routes["GET /bff/api/v1/classes"] = () => Response.json({ data: [], next_cursor: null });
  stub.routes["GET /bff/api/v1/fees/dues"] = (_request, url) =>
    Response.json(dues(url.searchParams.get("arrears_only") === "true"));
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("Earlier-year dues on the dues list (FR-FEE-031)", () => {
  it("shows the year of each row and the arrears tile", async () => {
    renderWithIntl(<DuesScreen />);
    expect(await screen.findByText("2025-26")).toBeInTheDocument();
    expect(screen.getByText("2026-27")).toBeInTheDocument();
    expect(screen.getByText(en.fees.dues.earlierYear)).toBeInTheDocument();
    expect(screen.getByText(en.fees.dues.arrearsTotal)).toBeInTheDocument();
  });

  it("filters to earlier years only (FR-FEE-032)", async () => {
    renderWithIntl(<DuesScreen />);
    await screen.findByText("2026-27");
    const form = screen.getByRole("search", { name: en.fees.dues.filterLabel });
    await userEvent.selectOptions(
      within(form).getByLabelText(en.fees.dues.years),
      en.fees.dues.arrearsOnly,
    );
    await waitFor(() =>
      expect(
        stub
          .callsTo("GET /bff/api/v1/fees/dues")
          .some((call) => call.url.searchParams.get("arrears_only") === "true"),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText("2026-27")).not.toBeInTheDocument());
  });
});
