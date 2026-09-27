import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SchoolAuditVerifyPage from "@/app/[locale]/(school)/audit/verify/page";
import type { Locale } from "@/i18n/routing";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { me } from "@/test/records-fixtures";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { AuditVerifyScreen } from "./AuditVerifyScreen";
import { AuditView } from "./AuditView";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/audit/verify",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  };
});

const VERIFY = "GET /bff/api/v1/audit/verify";
let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["audit.read"]));
});

afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("audit chain check (US-1001 AC2, FR-AUD-003, FR-AUD-005)", () => {
  it("runs only when asked and says how far the chain is verified", async () => {
    stub.routes[VERIFY] = () =>
      Response.json({ ok: true, checked: 1234, first_bad_seq: null, reason: null });
    renderWithIntl(<AuditVerifyScreen />);
    const button = await screen.findByRole("button", { name: "Check integrity" });
    expect(stub.callsTo(VERIFY)).toHaveLength(0);
    await userEvent.click(button);
    expect(await screen.findByText("The audit log is intact")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Verified up to event 1,234: 1,234 events were checked and none was changed.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(stub.callsTo(VERIFY)).toHaveLength(2));
  });

  it("says where the chain is broken, why, and what to do", async () => {
    stub.routes[VERIFY] = () =>
      Response.json({ ok: false, checked: 6, first_bad_seq: 7, reason: "hash_mismatch" });
    renderWithIntl(<AuditVerifyScreen />);
    await userEvent.click(await screen.findByRole("button", { name: "Check integrity" }));
    expect(await screen.findByText("The audit log is broken at event 7")).toBeInTheDocument();
    expect(
      screen.getByText("Events before it are intact (6 events, up to event 6)."),
    ).toBeInTheDocument();
    expect(screen.getByText("This event was changed after it was recorded.")).toBeInTheDocument();
    expect(screen.getByText(/Contact SchoolOS support now/)).toBeInTheDocument();
  });

  it("explains an unknown reason and an empty log plainly", async () => {
    stub.routes[VERIFY] = () =>
      Response.json({ ok: false, checked: 0, first_bad_seq: 1, reason: "new_reason" });
    renderWithIntl(<AuditVerifyScreen />, "te");
    await userEvent.click(
      await screen.findByRole("button", { name: messages.te.school.audit.verify }),
    );
    expect(
      await screen.findByText(messages.te.school.audit.integrity.reason.other),
    ).toBeInTheDocument();
    expect(
      screen.getByText(messages.te.school.audit.integrity.brokenFromStart),
    ).toBeInTheDocument();
  });

  it("shows a plain error when the check fails", async () => {
    stub.routes[VERIFY] = () => problem(503, "service_unavailable");
    renderWithIntl(<AuditVerifyScreen />);
    await userEvent.click(await screen.findByRole("button", { name: "Check integrity" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("The audit log is intact")).toBeNull();
  });

  it("is refused without audit.read and asks the API nothing", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["student.read_basic"]));
    renderWithIntl(<AuditVerifyScreen />);
    expect(await screen.findByText("You can't check the audit log")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check integrity" })).toBeNull();
    expect(stub.callsTo(VERIFY)).toHaveLength(0);
  });

  it("is linked from the audit log", () => {
    renderWithIntl(<AuditView events={{ status: "ready", data: [] }} />);
    expect(screen.getByRole("link", { name: "Check integrity" })).toHaveAttribute(
      "href",
      "/en/audit/verify",
    );
  });

  for (const locale of ["en", "te"] as Locale[]) {
    it(`renders the page [${locale}]`, async () => {
      renderWithIntl(SchoolAuditVerifyPage(), locale);
      expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
        messages[locale].school.audit.integrity.title,
      );
    });
  }
});
