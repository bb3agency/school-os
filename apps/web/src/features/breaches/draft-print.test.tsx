import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { me } from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { DraftPrintScreen, draftPrintHref } from "./DraftPrintScreen";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/privacy/breaches",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/** D60 (ADR-0050, FR-DPDP-036): the A4 print page of a breach draft. Synthetic data only. */
const bm = messages.en.breaches;
const B1 = "0192f3a4-0000-7000-8000-00000000b001";
const TENANT = me([]).tenant_id;
const DRAFT_CALL = `GET /bff/api/v1/privacy/breaches/${B1}/drafts/family_notice`;
let stub: BffStub;

function setMe(permissions: readonly string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
}

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/me/schools"] = () =>
    Response.json({ data: [{ tenant_id: TENANT, name: "Synthetic Vidya Niketan, Guntur" }] });
  stub.routes[DRAFT_CALL] = () =>
    Response.json({
      draft: "family_notice",
      template_version: "1.0",
      title: "Notice to families about a personal data breach",
      text: "Dear parent,\n\nOn 20/10/2026 a printed attendance list was left on a bus.\n\nPrivacy contact: [not set]",
    });
});
afterEach(() => {
  uninstallBffStub();
});

describe("D60 breach draft print page", () => {
  it("links from the breach to /privacy/breaches/{id}/drafts/{draft}/print", () => {
    expect(draftPrintHref(B1, "board_outline")).toBe(
      `/privacy/breaches/${B1}/drafts/board_outline/print`,
    );
  });

  it("shows the draft under the school's name and hides the chrome in print", async () => {
    setMe(["privacy.breach.read"]);
    const print = vi.fn();
    vi.stubGlobal("print", print);
    const user = userEvent.setup();
    const { container } = renderWithIntl(<DraftPrintScreen breachId={B1} draft="family_notice" />);
    const sheet = await screen.findByRole("article");
    expect(
      within(sheet).getByText("Notice to families about a personal data breach"),
    ).toBeInTheDocument();
    expect(within(sheet).getByText("Synthetic Vidya Niketan, Guntur")).toBeInTheDocument();
    expect(within(sheet).getByText(/left on a bus/)).toBeInTheDocument();
    expect(within(sheet).getByText("Template 1.0")).toBeInTheDocument();
    // Nothing inside the sheet is hidden in print; the header, crumbs and buttons are.
    expect(sheet.closest('[data-print="hide"]')).toBeNull();
    expect(sheet.querySelector('[data-print="hide"]')).toBeNull();
    const button = screen.getByRole("button", { name: bm.print.print });
    expect(button.closest('[data-print="hide"]')).not.toBeNull();
    const crumbs = container.querySelector("nav");
    expect(crumbs?.closest('[data-print="hide"]')).not.toBeNull();
    expect(screen.getByText(bm.drafts.notSent)).toHaveAttribute("data-print", "hide");
    await user.click(button);
    expect(print).toHaveBeenCalledTimes(1);
    expect(stub.callsTo(DRAFT_CALL)).toHaveLength(1);
  });

  it("asks for nothing without privacy.breach.read", async () => {
    setMe(["privacy.read"]);
    renderWithIntl(<DraftPrintScreen breachId={B1} draft="family_notice" />);
    expect(await screen.findByText(bm.noPermissionTitle)).toBeInTheDocument();
    expect(stub.callsTo(DRAFT_CALL)).toHaveLength(0);
    expect(screen.queryByRole("button", { name: bm.print.print })).toBeNull();
  });

  it("explains a draft that cannot be loaded and offers no Print button", async () => {
    setMe(["privacy.breach.read"]);
    stub.routes[DRAFT_CALL] = () => problem(404, "not_found");
    renderWithIntl(<DraftPrintScreen breachId={B1} draft="family_notice" />);
    expect(await screen.findByText(messages.en.errors.api.not_found.title)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: bm.print.print })).toBeNull();
    expect(screen.queryByRole("article")).toBeNull();
  });
});
