import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { expectOneCall, fireTwice } from "@/test/double-submit";
import { expectPassivePolls } from "@/test/poll";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me, SECTION, STUDENT, structureRoutes } from "@/test/school-fixtures";
import { AccountScreen } from "./AccountScreen";
import { COLLECT, FINANCE_READ, MANAGE, type Account, type FeeStatus, type Head } from "./data";
import { SetupScreen } from "./SetupScreen";
import { financialYears, setTaxPollDelayForTesting, taxIssueError } from "./tax";
import type { TaxCertificate, TaxCertificateBulk, TaxCertificateListItem } from "./tax";
import { TaxCertificatePanel, TaxCertificatesScreen } from "./TaxCertificates";
import { ApiError } from "@/lib/bff/query";

/**
 * Tuition fee certificate staff screens (FM-06; FR-FEE-046..056). Synthetic names, serials and
 * amounts only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/fees/tax-certificates",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.fees.taxCertificate;
const re = (text: string) => new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
const ACCOUNT = "0192f3a4-0000-7000-8000-00000000f001";
const CERT = "0192f3a4-0000-7000-8000-00000000fa01";
const OLD_CERT = "0192f3a4-0000-7000-8000-00000000fa02";
const CLASS = "0192f3a4-0000-7000-8000-0000000000c9";
const LIST = "GET /bff/api/v1/fees/tax-certificates";
const ISSUE = "POST /bff/api/v1/fees/tax-certificates";
const BULK = "POST /bff/api/v1/fees/tax-certificates/bulk";

const STATUS: FeeStatus = {
  enabled: true,
  today: "2026-11-09",
  financial_year: "2026-27",
  day_closed: false,
  paper_size: "A5",
};

function account(): Account {
  return {
    id: ACCOUNT,
    student_id: STUDENT,
    display_name: "Synthetica Tax Student",
    admission_no: "F-0001",
    class_section: "Class IX A",
    academic_year_id: "0192f3a4-0000-7000-8000-00000000f301",
    plan_id: "0192f3a4-0000-7000-8000-00000000f302",
    plan_name: "Class IX 2026-27",
    category_code: null,
    status: "open",
    lines: [],
    totals: {
      charged: "0.00",
      concessions: "0.00",
      late_fees: "0.00",
      adjustments: "0.00",
      paid: "0.00",
      refunded: "0.00",
      balance: "0.00",
      overdue: "0.00",
      collectable: "0.00",
    },
    pending_requests: 0,
    version: 1,
  };
}

function item(overrides: Partial<TaxCertificateListItem> = {}): TaxCertificateListItem {
  return {
    id: CERT,
    serial_number: "FTC/2026-27/00007",
    financial_year: "2026-27",
    as_of: "2026-11-09",
    student_id: STUDENT,
    student_name: "Synthetica Tax Student",
    class_section: "Class IX A",
    amount: "36000.00",
    receipt_count: 3,
    status: "issued",
    issued_at: "2026-11-09T05:00:00Z",
    pdf_status: "ready",
    ...overrides,
  };
}

function issued(overrides: Partial<TaxCertificate> = {}): TaxCertificate {
  return {
    id: CERT,
    serial_number: "FTC/2026-27/00007",
    financial_year: "2026-27",
    as_of: "2026-11-09",
    in_progress: true,
    student_id: STUDENT,
    student_name: "Synthetica Tax Student",
    admission_no: "F-0001",
    class_section: "Class IX A",
    paid: "36000.00",
    refunded: "0.00",
    amount: "36000.00",
    amount_in_words: "Rupees Thirty Six Thousand Only",
    receipts: [],
    head_names: ["Tuition fee"],
    status: "issued",
    superseded_by: null,
    issued_by: "0192f3a4-0000-7000-8000-0000000000b9",
    issued_by_name: "Synthetic Clerk",
    issued_at: "2026-11-09T05:00:00Z",
    pdf_status: "queued",
    content_sha256: "0".repeat(64),
    legal_verified: false,
    outcome: "issued",
    ...overrides,
  };
}

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/fees/status"] = () => Response.json(STATUS);
});
afterEach(() => {
  setTaxPollDelayForTesting(null);
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(permissions, { roles: ["office_staff"] }));
}

describe("financial years (FR-FEE-048)", () => {
  it("offers the current year and the 8 before it, never a year that has not started", () => {
    const years = financialYears("2026-27");
    expect(years).toHaveLength(9);
    expect(years[0]).toBe("2026-27");
    expect(years.at(-1)).toBe("2018-19");
    expect(years).not.toContain("2027-28");
    expect(financialYears("1999-00x")).toEqual([]);
    expect(financialYears("2099-00")).toContain("2098-99");
  });

  it("turns a 422 on financial_year into its own plain-language code", () => {
    const failure = new ApiError(422, "validation_error", {
      code: "validation_error",
      errors: [{ field: "financial_year", code: "too_old", message_key: "errors.too_old" }],
    });
    expect((taxIssueError(failure) as ApiError).code).toBe("financial_year_too_old");
    const other = new ApiError(409, "no_tuition_paid", {});
    expect(taxIssueError(other)).toBe(other);
  });
});

describe("fee account: tax certificate tab (FR-FEE-049, FR-FEE-052)", () => {
  it("issues the certificate of the chosen year once, with the not-tax-advice note", async () => {
    signedIn([COLLECT]);
    stub.routes[LIST] = () => page([]);
    stub.routes[ISSUE] = () => Response.json(issued(), { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<TaxCertificatePanel account={account()} />);
    const year = await screen.findByLabelText(en.year);
    expect(within(year).getAllByRole("option")).toHaveLength(9);
    expect(screen.getByText(en.notAdvice)).toBeInTheDocument();
    expect(await screen.findByText("No certificate issued for FY 2026-27 yet.")).toBeVisible();
    await user.selectOptions(year, "2025-26");
    await waitFor(() =>
      expect(
        stub.callsTo(LIST).some((c) => c.url.searchParams.get("financial_year") === "2025-26"),
      ).toBe(true),
    );
    const listCall = stub.callsTo(LIST).at(-1);
    expect(listCall?.url.searchParams.get("student_id")).toBe(STUDENT);
    await user.click(await screen.findByRole("button", { name: en.issue }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(en.notAdvice)).toBeInTheDocument();
    expect(within(dialog).getByText(en.issueConsequence)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: en.issue });
    fireTwice(confirm.closest("form") as HTMLFormElement, "submit");
    await expectOneCall(stub, ISSUE);
    const call = stub.callsTo(ISSUE)[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      student_id: STUDENT,
      financial_year: "2025-26",
    });
    expect(call?.headers.get("Idempotency-Key")).toBeTruthy();
  });

  it("says when nothing changed on issuing again (outcome unchanged)", async () => {
    signedIn([COLLECT]);
    stub.routes[LIST] = () => page([item()]);
    stub.routes[ISSUE] = () =>
      Response.json(issued({ outcome: "unchanged", pdf_status: "ready" }), { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<TaxCertificatePanel account={account()} />);
    expect(await screen.findAllByText("FTC/2026-27/00007")).not.toHaveLength(0);
    await user.click(screen.getByRole("button", { name: en.issueAgain }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(en.issueAgainConsequence)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: en.issueAgain }));
    expect(
      await screen.findByText(
        "Nothing changed since certificate FTC/2026-27/00007 was issued, so it stays as it is.",
      ),
    ).toBeInTheDocument();
  });

  it.each([
    [409, "no_tuition_paid", {}],
    [409, "too_many_receipts", {}],
    [
      422,
      "validation_error",
      {
        errors: [
          { field: "financial_year", code: "not_started", message_key: "errors.not_started" },
        ],
      },
    ],
  ] as const)("explains a %i %s in plain words with how to fix it", async (status, code, extra) => {
    signedIn([COLLECT]);
    stub.routes[LIST] = () => page([]);
    stub.routes[ISSUE] = () => problem(status, code, extra);
    const user = userEvent.setup();
    renderWithIntl(<TaxCertificatePanel account={account()} />);
    await user.click(await screen.findByRole("button", { name: en.issue }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: en.issue }));
    const key = code === "validation_error" ? "financial_year_not_started" : code;
    const message = en.errors[key as keyof typeof en.errors];
    expect(await within(dialog).findByText(message.title)).toBeInTheDocument();
    expect(within(dialog).getByText(message.body)).toBeInTheDocument();
  });

  it("shows a reader (finance.read) the certificate, print view and download, but no issue or retry", async () => {
    signedIn([FINANCE_READ]);
    stub.routes[LIST] = () =>
      page([
        item(),
        item({ id: OLD_CERT, serial_number: "FTC/2026-27/00002", status: "superseded" }),
      ]);
    stub.routes[`GET /bff/api/v1/fees/tax-certificates/${CERT}/download-url`] = () =>
      Response.json({
        url: "javascript:alert(1)",
        expires_at: "2026-11-09T05:05:00Z",
        filename: "t.pdf",
      });
    const user = userEvent.setup();
    renderWithIntl(<TaxCertificatePanel account={account()} />);
    expect(await screen.findAllByText("FTC/2026-27/00007")).not.toHaveLength(0);
    expect(screen.queryByRole("button", { name: en.issue })).toBeNull();
    expect(screen.queryByRole("button", { name: en.issueAgain })).toBeNull();
    expect(screen.getByText(re("Earlier certificates for FY 2026-27"))).toBeInTheDocument();
    const prints = screen.getAllByRole("link", { name: re(en.print) });
    expect(prints[0]).toHaveAttribute("href", `/bff/api/v1/fees/tax-certificates/${CERT}/print`);
    expect(prints[0]).toHaveAttribute("target", "_blank");
    // SEC-010: a link that is not https is refused, never opened.
    await user.click(screen.getByRole("button", { name: re(en.download) }));
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
  });

  it("reads again passively while the PDF is made, then offers the download", async () => {
    setTaxPollDelayForTesting(() => 20);
    signedIn([COLLECT]);
    let reads = 0;
    stub.routes[LIST] = () => {
      reads += 1;
      return page([item({ pdf_status: reads < 3 ? "queued" : "ready" })]);
    };
    renderWithIntl(<TaxCertificatePanel account={account()} />);
    expect(await screen.findByText(en.pdf.preparing)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: re(en.download) })).toBeNull();
    expect(
      await screen.findByRole("button", { name: re(en.download) }, { timeout: 3000 }),
    ).toBeInTheDocument();
    expectPassivePolls(stub.callsTo(LIST));
    const settled = stub.callsTo(LIST).length;
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(stub.callsTo(LIST)).toHaveLength(settled);
  });

  it("makes a failed PDF again with one request (fee.collect)", async () => {
    signedIn([COLLECT]);
    stub.routes[LIST] = () => page([item({ pdf_status: "failed" })]);
    stub.routes[`POST /bff/api/v1/fees/tax-certificates/${CERT}/render`] = () =>
      Response.json(issued());
    renderWithIntl(<TaxCertificatePanel account={account()} />);
    expect(await screen.findByText(en.pdf.failed)).toBeInTheDocument();
    expect(screen.getByText(en.pdfFailedBody)).toBeInTheDocument();
    fireTwice(screen.getByRole("button", { name: re(en.retry) }), "click");
    await expectOneCall(stub, `POST /bff/api/v1/fees/tax-certificates/${CERT}/render`);
  });

  it("is a tab of the fee account", async () => {
    signedIn([COLLECT]);
    stub.routes[`GET /bff/api/v1/fees/accounts/${ACCOUNT}`] = () => Response.json(account());
    stub.routes[LIST] = () => page([]);
    const user = userEvent.setup();
    renderWithIntl(<AccountScreen accountId={ACCOUNT} />);
    await user.click(await screen.findByRole("tab", { name: en.tab }));
    expect(await screen.findByRole("heading", { name: en.panelTitle })).toBeVisible();
  });
});

describe("certificates list and bulk issue (FR-FEE-050, FR-FEE-055)", () => {
  function withStructure() {
    Object.assign(stub.routes, structureRoutes());
  }

  it("issues for a section and reports what happened", async () => {
    signedIn([COLLECT]);
    withStructure();
    stub.routes[LIST] = () => page([item()]);
    const result: TaxCertificateBulk = {
      financial_year: "2026-27",
      students: 40,
      issued: 30,
      reissued: 2,
      unchanged: 5,
      skipped_no_tuition: 3,
      certificate_ids: [],
    };
    stub.routes[BULK] = () => Response.json(result);
    const user = userEvent.setup();
    renderWithIntl(<TaxCertificatesScreen />);
    await user.click(await screen.findByRole("button", { name: en.bulk }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(en.notAdvice)).toBeInTheDocument();
    expect(within(dialog).getByText(en.bulkConsequence)).toBeInTheDocument();
    const target = within(dialog).getByLabelText(en.bulkTarget);
    await waitFor(() => expect(within(target).getAllByRole("option")).toHaveLength(3));
    await user.selectOptions(target, `section:${SECTION}`);
    const form = within(dialog).getByRole("button", { name: en.bulkConfirm }).closest("form");
    fireTwice(form as HTMLFormElement, "submit");
    await expectOneCall(stub, BULK);
    expect(JSON.parse(stub.callsTo(BULK)[0]?.body ?? "{}")).toEqual({
      financial_year: "2026-27",
      section_id: SECTION,
    });
    expect(await within(dialog).findByText("Done for FY 2026-27")).toBeInTheDocument();
    for (const [label, value] of [
      [en.result.issued, "30"],
      [en.result.reissued, "2"],
      [en.result.unchanged, "5"],
      [en.result.skipped, "3"],
    ] as const) {
      const row = within(dialog).getByText(label).closest("div") as HTMLElement;
      expect(within(row).getByText(value)).toBeInTheDocument();
    }
    expect(within(dialog).getByText(en.result.skippedHint)).toBeInTheDocument();
  });

  it("sends a whole class as class_id and explains too_many_students", async () => {
    signedIn([COLLECT]);
    withStructure();
    stub.routes[LIST] = () => page([]);
    stub.routes[BULK] = () => problem(409, "too_many_students");
    const user = userEvent.setup();
    renderWithIntl(<TaxCertificatesScreen />);
    await user.click(await screen.findByRole("button", { name: en.bulk }));
    const dialog = await screen.findByRole("dialog");
    const target = within(dialog).getByLabelText(en.bulkTarget);
    await waitFor(() => expect(within(target).getAllByRole("option")).toHaveLength(3));
    await user.selectOptions(target, `class:${CLASS}`);
    await user.click(within(dialog).getByRole("button", { name: en.bulkConfirm }));
    expect(await within(dialog).findByText(en.errors.too_many_students.title)).toBeInTheDocument();
    expect(JSON.parse(stub.callsTo(BULK)[0]?.body ?? "{}")).toEqual({
      financial_year: "2026-27",
      class_id: CLASS,
    });
  });

  it("asks for a class or section before sending", async () => {
    signedIn([COLLECT]);
    withStructure();
    stub.routes[LIST] = () => page([]);
    const user = userEvent.setup();
    renderWithIntl(<TaxCertificatesScreen />);
    await user.click(await screen.findByRole("button", { name: en.bulk }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: en.bulkConfirm }));
    expect(within(dialog).getByLabelText(en.bulkTarget)).toHaveAttribute("aria-invalid", "true");
    expect(stub.callsTo(BULK)).toHaveLength(0);
  });

  it("lists for a reader with filters by codes and ids only, and no bulk issue", async () => {
    signedIn([FINANCE_READ]);
    withStructure();
    stub.routes[LIST] = () =>
      Response.json({
        data: [
          item(),
          item({ id: OLD_CERT, serial_number: "FTC/2026-27/00002", status: "superseded" }),
        ],
        next_cursor: "next-page",
      });
    const user = userEvent.setup();
    renderWithIntl(<TaxCertificatesScreen />);
    expect(await screen.findAllByText("FTC/2026-27/00007")).not.toHaveLength(0);
    expect(screen.queryByRole("button", { name: en.bulk })).toBeNull();
    expect(screen.queryByRole("button", { name: re(en.retry) })).toBeNull();
    await user.selectOptions(screen.getByLabelText(en.statusLabel), "superseded");
    await user.selectOptions(screen.getByLabelText(en.year), "2025-26");
    await waitFor(() => {
      const last = stub.callsTo(LIST).at(-1);
      expect(last?.url.searchParams.get("status")).toBe("superseded");
      expect(last?.url.searchParams.get("financial_year")).toBe("2025-26");
    });
    await user.click(screen.getByRole("button", { name: messages.en.ui.pagination.next }));
    await waitFor(() =>
      expect(stub.callsTo(LIST).at(-1)?.url.searchParams.get("cursor")).toBe("next-page"),
    );
    for (const call of stub.callsTo(LIST)) {
      expect(call.url.search).not.toMatch(/Synthetica/);
    }
  });

  it("tells a teacher who can see certificates, without asking the API for them", async () => {
    signedIn(["student.read_basic"]);
    withStructure();
    renderWithIntl(<TaxCertificatesScreen />);
    expect(await screen.findByText(messages.en.fees.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo(LIST)).toHaveLength(0);
  });

  it("says fees are off (404 module_hidden)", async () => {
    signedIn([COLLECT]);
    withStructure();
    stub.routes["GET /bff/api/v1/fees/status"] = () => problem(404, "module_hidden");
    stub.routes[LIST] = () => problem(404, "module_hidden");
    renderWithIntl(<TaxCertificatesScreen />);
    expect(await screen.findByText(messages.en.fees.off.title)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.bulk })).toBeNull();
  });
});

describe("fee heads: counts as tuition for tax (FR-FEE-047)", () => {
  const HEAD: Head = {
    id: "0192f3a4-0000-7000-8000-00000000f101",
    code: "TUI",
    name: "Tuition fee",
    kind: "tuition",
    gst_exempt: true,
    tax_tuition: true,
    sort_order: 10,
    active: true,
    version: 4,
  };

  function setupRoutes() {
    stub.routes["GET /bff/api/v1/fees/heads"] = () => Response.json([HEAD]);
    stub.routes["GET /bff/api/v1/fees/plans"] = () => Response.json([]);
    stub.routes["GET /bff/api/v1/academic-years"] = () => page([]);
    stub.routes["GET /bff/api/v1/classes"] = () => page([]);
  }

  it("follows the kind on a new head until changed, and sends tax_tuition", async () => {
    signedIn([MANAGE]);
    setupRoutes();
    stub.routes["POST /bff/api/v1/fees/heads"] = () =>
      Response.json({ ...HEAD, id: "x", code: "EXAM" }, { status: 201 });
    const user = userEvent.setup();
    renderWithIntl(<SetupScreen />);
    await user.click(await screen.findByRole("button", { name: messages.en.fees.setup.addHead }));
    const dialog = await screen.findByRole("dialog");
    const box = within(dialog).getByRole("checkbox", { name: en.headToggle });
    expect(box).toBeChecked();
    await user.selectOptions(
      within(dialog).getByLabelText(messages.en.fees.setup.headKind),
      "exam",
    );
    expect(box).not.toBeChecked();
    await user.type(within(dialog).getByLabelText(re(messages.en.fees.setup.headCode)), "EXAM");
    await user.type(within(dialog).getByLabelText(messages.en.fees.setup.headName), "Exam fee");
    await user.click(within(dialog).getByRole("button", { name: messages.en.fees.setup.addHead }));
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/fees/heads")).toHaveLength(1));
    const body = JSON.parse(stub.callsTo("POST /bff/api/v1/fees/heads")[0]?.body ?? "{}");
    expect(body).toMatchObject({ kind: "exam", tax_tuition: false });
  });

  it("changes the setting of a head with If-Match (fee.manage only)", async () => {
    signedIn([MANAGE]);
    setupRoutes();
    const path = `PATCH /bff/api/v1/fees/heads/${HEAD.id}`;
    stub.routes[path] = () => Response.json({ ...HEAD, tax_tuition: false, version: 5 });
    const user = userEvent.setup();
    renderWithIntl(<SetupScreen />);
    await user.click(await screen.findByRole("button", { name: en.editHead }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(en.editHeadConsequence)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("checkbox", { name: en.headToggle }));
    await user.click(within(dialog).getByRole("button", { name: en.save }));
    await waitFor(() => expect(stub.callsTo(path)).toHaveLength(1));
    const call = stub.callsTo(path)[0];
    expect(call?.headers.get("If-Match")).toBe('W/"4"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ tax_tuition: false });
  });

  it("shows the setting to a reader without the change button", async () => {
    signedIn([FINANCE_READ]);
    setupRoutes();
    renderWithIntl(<SetupScreen />);
    expect(await screen.findByText(en.headColumn)).toBeInTheDocument();
    expect(screen.getByText(en.yes)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.editHead })).toBeNull();
  });
});
