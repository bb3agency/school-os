import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { FamilyPanel } from "./FamilyCounter";
import type { FamilyCounter } from "./family";
import { StructureNoticeCard } from "./StructureNotice";

/**
 * FM-07 family counter (FR-FEE-101..107, FR-FEE-124) and FM-10 fee structure notice
 * (FR-FEE-113..124). Synthetic names and amounts only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/fees/collect",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const fam = messages.en.fees.counter.family;
const sn = messages.en.fees.structureNotice;
const ANU = "0192f3a4-0000-7000-8000-00000000a701";
const RAVI = "0192f3a4-0000-7000-8000-00000000a702";
const ACC_ANU = "0192f3a4-0000-7000-8000-00000000a711";
const ACC_RAVI = "0192f3a4-0000-7000-8000-00000000a712";
const YEAR = "0192f3a4-0000-7000-8000-00000000a721";
const OLD = "0192f3a4-0000-7000-8000-00000000a722";
const IX = "0192f3a4-0000-7000-8000-00000000a731";

function due(account: string, amount: string) {
  return {
    account_id: account,
    academic_year_id: YEAR,
    academic_year_label: "2026-27",
    earlier_year: false,
    balance: amount,
    overdue: amount,
    collectable: amount,
    oldest_due_on: "2026-07-15",
  };
}

function family(withSibling = true): FamilyCounter {
  const members = [
    {
      student_id: ANU,
      display_name: "Synthetica Anu",
      admission_no: "F-0701",
      class_section: "Class IX A",
      status: "active",
      selected: true,
      linked_by: [],
      accounts: [due(ACC_ANU, "3500.00")],
      collectable: "3500.00",
    },
    {
      student_id: RAVI,
      display_name: "Synthetica Ravi",
      admission_no: "F-0702",
      class_section: "Class VI B",
      status: "active",
      selected: false,
      linked_by: ["father"],
      accounts: [due(ACC_RAVI, "2000.00")],
      collectable: "2000.00",
    },
  ];
  return {
    student_id: ANU,
    as_of: "2026-11-12",
    members: withSibling ? members : members.slice(0, 1),
    total_collectable: "5500.00",
    max_accounts: 8,
    modes: ["cash", "cheque", "bank_transfer"],
  };
}

function receipt(id: string, number: string, account: string, student: string, name: string) {
  return {
    id,
    receipt_number: number,
    amount: "1000.00",
    account_id: account,
    student_id: student,
    student_name: name,
    class_section: "Class IX A",
  };
}

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes[`GET /bff/api/v1/fees/students/${ANU}/family`] = () => Response.json(family());
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("Family at the counter (FR-FEE-101..107)", () => {
  it("lists the siblings and saves one receipt per child in one payment", async () => {
    const user = userEvent.setup();
    stub.routes["POST /bff/api/v1/fees/family-payments"] = () =>
      Response.json(
        {
          receipts: [
            receipt("r1", "RCT/2026-27/0101", ACC_ANU, ANU, "Synthetica Anu"),
            receipt("r2", "RCT/2026-27/0102", ACC_RAVI, RAVI, "Synthetica Ravi"),
          ],
          amount: "2000.00",
          mode: "cash",
        },
        { status: 201 },
      );
    renderWithIntl(<FamilyPanel studentId={ANU} />);
    const region = await screen.findByRole("region", { name: fam.title });
    const rows = within(region).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("Synthetica Anu");
    expect(rows[1]).toHaveTextContent("Synthetica Ravi");
    await user.type(screen.getByLabelText("Amount paid for Synthetica Anu, 2026-27"), "1000");
    await user.type(screen.getByLabelText("Amount paid for Synthetica Ravi, 2026-27"), "1000");
    await user.click(screen.getByRole("button", { name: "Save 2 receipts" }));
    expect(await screen.findByText(fam.savedTitle)).toBeInTheDocument();
    const [call] = stub.callsTo("POST /bff/api/v1/fees/family-payments");
    expect(call?.headers.get("idempotency-key")).toBeTruthy();
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      mode: "cash",
      mode_details: {},
      parts: [
        { account_id: ACC_ANU, amount: "1000.00" },
        { account_id: ACC_RAVI, amount: "1000.00" },
      ],
    });
    expect(screen.getByRole("link", { name: fam.printSlip })).toHaveAttribute(
      "href",
      "/bff/api/v1/fees/family-payments/slip?receipt_ids=r1&receipt_ids=r2",
    );
    expect(
      screen.getByRole("link", { name: "Print receipt RCT/2026-27/0102" }),
    ).toBeInTheDocument();
  });

  it("checks each amount against what is due before sending anything", async () => {
    const user = userEvent.setup();
    renderWithIntl(<FamilyPanel studentId={ANU} />);
    await screen.findByRole("region", { name: fam.title });
    await user.type(screen.getByLabelText("Amount paid for Synthetica Anu, 2026-27"), "100");
    await user.type(screen.getByLabelText("Amount paid for Synthetica Ravi, 2026-27"), "9999");
    await user.click(screen.getByRole("button", { name: "Save 2 receipts" }));
    expect(await screen.findByText(fam.errors.above_due)).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/fees/family-payments")).toHaveLength(0);
  });

  it("explains a refusal in plain words", async () => {
    const user = userEvent.setup();
    stub.routes["POST /bff/api/v1/fees/family-payments"] = () =>
      problem(422, "validation_error", {
        errors: [{ field: "parts.1.account_id", code: "not_in_family", message_key: "errors.x" }],
      });
    renderWithIntl(<FamilyPanel studentId={ANU} />);
    await screen.findByRole("region", { name: fam.title });
    await user.click(screen.getByRole("button", { name: fam.fillAll }));
    await user.click(screen.getByRole("button", { name: "Save 2 receipts" }));
    expect(await screen.findByText(fam.errors.not_in_family)).toBeInTheDocument();
  });

  it("shows nothing when the student has no sibling", async () => {
    stub.routes[`GET /bff/api/v1/fees/students/${ANU}/family`] = () => Response.json(family(false));
    renderWithIntl(<FamilyPanel studentId={ANU} />);
    await waitFor(() =>
      expect(stub.callsTo(`GET /bff/api/v1/fees/students/${ANU}/family`)).toHaveLength(1),
    );
    expect(screen.queryByRole("region", { name: fam.title })).toBeNull();
  });
});

describe("Fee structure notice (FR-FEE-113..124)", () => {
  it("prints by year and flags increases above the review level", async () => {
    stub.routes["GET /bff/api/v1/fees/structure-changes"] = () =>
      Response.json({
        academic_year_id: YEAR,
        academic_year_label: "2026-27",
        previous_year_id: OLD,
        previous_year_label: "2025-26",
        warn_percent: "10.0",
        warn_percent_verified: false,
        classes_above_threshold: 1,
        classes: [
          {
            class_id: IX,
            class_label: "Class IX",
            plan_status: "draft",
            has_previous: true,
            previous_total: "6500.00",
            current_total: "9000.00",
            difference: "2500.00",
            percent: "38.5",
            above_threshold: true,
            heads: [
              {
                head_id: "h1",
                code: "TUI",
                name: "Tuition fee",
                change: "changed",
                previous_amount: "5000.00",
                current_amount: "6000.00",
                difference: "1000.00",
                percent: "20.0",
                above_threshold: true,
              },
              {
                head_id: "h2",
                code: "BUS",
                name: "Transport fee",
                change: "added",
                previous_amount: null,
                current_amount: "2000.00",
                difference: "2000.00",
                percent: null,
                above_threshold: false,
              },
            ],
          },
        ],
      });
    renderWithIntl(
      <StructureNoticeCard
        years={[{ id: YEAR, label: "2026-27", is_current: true }]}
        classes={[{ id: IX, display_en: "Class IX" }]}
      />,
    );
    expect(await screen.findByText("Tuition fee")).toBeInTheDocument();
    expect(screen.getByText("+20.0%")).toBeInTheDocument();
    expect(screen.getByText(sn.kinds.added)).toBeInTheDocument();
    expect(screen.getByText("1 class has an increase above the review level")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: sn.print })).toHaveAttribute(
      "href",
      `/bff/api/v1/fees/structure-notice/print?academic_year_id=${YEAR}`,
    );
    const [call] = stub.callsTo("GET /bff/api/v1/fees/structure-changes");
    expect(call?.url.searchParams.get("academic_year_id")).toBe(YEAR);
  });
});
