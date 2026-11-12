import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { me, STUDENT } from "@/test/school-fixtures";
import { logRequestBody, logRequestSchema, prepareHref, type TcRequest } from "./data";
import { TcRequestsScreen } from "./TcRequestsScreen";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/certificates/tc-requests",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

// Synthetic data only (CLAUDE.md §6.11).
const REQ = "0192f3a4-0000-7000-8000-0000000c0901";
const CERT = "0192f3a4-0000-7000-8000-0000000ce901";

function request(overrides: Partial<TcRequest> = {}): TcRequest {
  return {
    id: REQ,
    student_id: STUDENT,
    student_name: "Synthetica Asha Tset",
    admission_no: "A-101",
    class_label: "Class 9 A",
    status: "received",
    received_on: "2026-11-02",
    channel: "paper",
    leaving_date: null,
    leaving_reason: null,
    certificate_id: null,
    certificate_status: null,
    certificate_serial: null,
    received_by_name: "Synthetic Clerk Lakshmi",
    received_at: "2026-11-02T04:30:00Z",
    preparing_by_name: null,
    preparing_at: null,
    ready_by_name: null,
    ready_at: null,
    issued_by_name: null,
    issued_at: null,
    cancelled_by_name: null,
    cancelled_at: null,
    cancel_reason: null,
    days_open: 10,
    overdue: true,
    fee_note: null,
    can_prepare: true,
    can_hand_over: false,
    can_cancel: true,
    version: 1,
    ...overrides,
  };
}

function list(rows: TcRequest[], overdue = 1) {
  return Response.json({
    data: rows,
    next_cursor: null,
    overdue_count: overdue,
    overdue_after_days: 7,
    overdue_verified: false,
  });
}

let stub: BffStub;
beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("TC request helpers (FR-CERT-026)", () => {
  it("leaves optional values out and links to the issue screen", () => {
    const parsed = logRequestSchema.parse({
      student_id: STUDENT,
      received_on: "2026-11-10",
      channel: "phone",
      leaving_date: "",
      leaving_reason: "",
    });
    expect(logRequestBody(parsed)).toEqual({
      student_id: STUDENT,
      received_on: "2026-11-10",
      channel: "phone",
    });
    expect(prepareHref({ student_id: STUDENT })).toBe(
      `/students/${STUDENT}/certificates/new?type=transfer`,
    );
  });
});

describe("TC requests screen (FR-CERT-035..FR-CERT-037, FR-CERT-044)", () => {
  it("flags overdue requests and offers the clerk's next steps", async () => {
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(me(["certificate.read", "certificate.issue"]));
    stub.routes["GET /bff/api/v1/tc-requests"] = () => list([request()]);
    renderWithIntl(<TcRequestsScreen />);
    expect(await screen.findByText("1 request is overdue")).toBeInTheDocument();
    expect(screen.getByText(/your school has not confirmed yet/)).toBeInTheDocument();
    const row = screen
      .getAllByRole("row")
      .find((r) => within(r).queryByText("Synthetica Asha Tset"));
    expect(row).toBeDefined();
    const scoped = within(row as HTMLElement);
    expect(scoped.getByText("Overdue")).toBeInTheDocument();
    expect(scoped.getByText("10 days")).toBeInTheDocument();
    expect(scoped.getByRole("link", { name: /Prepare the TC/ })).toHaveAttribute(
      "href",
      `/students/${STUDENT}/certificates/new?type=transfer`,
    );
    expect(screen.getByRole("button", { name: "Log a request" })).toBeInTheDocument();
  });

  it("sends the overdue filter and marks a request as being prepared with If-Match", async () => {
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(me(["certificate.read", "certificate.issue"]));
    stub.routes["GET /bff/api/v1/tc-requests"] = () => list([request()]);
    stub.routes[`POST /bff/api/v1/tc-requests/${REQ}/status`] = () =>
      Response.json(request({ status: "in_preparation", can_prepare: false, version: 2 }));
    renderWithIntl(<TcRequestsScreen />);
    await userEvent.click(await screen.findByRole("button", { name: "Show overdue requests" }));
    await waitFor(() =>
      expect(
        stub
          .callsTo("GET /bff/api/v1/tc-requests")
          .some((call) => call.url.searchParams.get("overdue") === "true"),
      ).toBe(true),
    );
    await userEvent.click(await screen.findByRole("button", { name: /Mark as being prepared/ }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/tc-requests/${REQ}/status`)).toHaveLength(1),
    );
    const [call] = stub.callsTo(`POST /bff/api/v1/tc-requests/${REQ}/status`);
    expect(call?.headers.get("If-Match")).toBe('W/"1"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ status: "in_preparation" });
  });

  it("readers see the log without actions; a ready TC links to its certificate", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["certificate.read"]));
    stub.routes["GET /bff/api/v1/tc-requests"] = () =>
      list(
        [
          request({
            status: "ready",
            overdue: false,
            certificate_id: CERT,
            certificate_serial: "TC/2026-27/0004",
            ready_by_name: "Synthetic Principal Suresh",
            ready_at: "2026-11-05T06:00:00Z",
            can_prepare: false,
            can_cancel: false,
          }),
        ],
        0,
      );
    renderWithIntl(<TcRequestsScreen />);
    expect(await screen.findByRole("link", { name: /TC\/2026-27\/0004/ })).toHaveAttribute(
      "href",
      `/certificates/${CERT}`,
    );
    expect(screen.getByText(/by Synthetic Principal Suresh/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Log a request" })).toBeNull();
    expect(screen.queryByText(/overdue/i, { selector: "[role=alert] *" })).toBeNull();
  });
});
