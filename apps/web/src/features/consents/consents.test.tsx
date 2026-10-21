import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { registerStepUpHandler } from "@/lib/bff/step-up";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { ID, me } from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { ConsentsScreen } from "./ConsentsScreen";
import { PurposesScreen, versionWarning } from "./PurposesScreen";
import { formsHref, nextDecisions, type Purpose, type PurposeList } from "./types";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/settings/consent-purposes",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/**
 * Consent purposes and the consent register (ADR-0049; US-2601, US-2603; FR-DPDP-020..026).
 * Synthetic data only.
 */
const cm = messages.en.consents;
const PURPOSE = "0192f3a4-0000-7000-8000-00000000c0a1";
const SECTION = "0192f3a4-0000-7000-8000-00000000a9a1";
let stub: BffStub;
let unregister: (() => void) | undefined;

function purpose(extra: Partial<Purpose> = {}): Purpose {
  return {
    id: PURPOSE,
    key: "website_photos",
    label_en: "Photos on the school website",
    label_te: null,
    description: "Class photos on the school's public website, kept for one academic year.",
    active: true,
    answered: 12,
    current_version_answered: true,
    form_version: 2,
    suggested_key: "website_photos",
    version: 4,
    ...extra,
  };
}

function list(extra: Partial<PurposeList> = {}): PurposeList {
  return {
    data: [purpose()],
    suggestions: [
      {
        key: "website_photos",
        label_en: "Photos on the school website",
        label_te: null,
        description: "Class photos on the website.",
        enabled: true,
      },
      {
        key: "whatsapp_group",
        label_en: "The class WhatsApp group",
        label_te: null,
        description: "Adding a parent's number to the class group.",
        enabled: false,
      },
    ],
    ...extra,
  };
}

function setMe(permissions: readonly string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
}

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/academic-years"] = () => page([]);
  stub.routes["GET /bff/api/v1/classes"] = () => page([]);
  stub.routes["GET /bff/api/v1/sections"] = () => page([]);
});
afterEach(() => {
  unregister?.();
  unregister = undefined;
  uninstallBffStub();
});

describe("FR-DPDP-021 decisions offered and print links", () => {
  it("withdrawal only after agreement; never back to the form after a decision", () => {
    expect(nextDecisions("not_answered")).toEqual(["pending", "given", "refused"]);
    expect(nextDecisions("pending")).toEqual(["pending", "given", "refused"]);
    expect(nextDecisions("given")).toEqual(["given", "refused", "withdrawn"]);
    expect(nextDecisions("refused")).toEqual(["given", "refused"]);
  });

  it("print views carry ids and codes only", () => {
    expect(formsHref({ sectionId: SECTION }, { onlyUnanswered: true })).toBe(
      `/bff/api/v1/consents/forms?section_id=${SECTION}&only_unanswered=true`,
    );
    expect(formsHref({ studentId: ID.student }, { language: "te" })).toBe(
      `/bff/api/v1/consents/forms?student_id=${ID.student}&language=te`,
    );
  });

  it("warns about a new form version only when the current one was answered", () => {
    expect(versionWarning(purpose())).toEqual({ next: 3, current: 2 });
    expect(versionWarning(purpose({ current_version_answered: false }))).toBeNull();
  });
});

describe("US-2601 consent purposes", () => {
  it("editing an answered purpose says it makes the next version of the form", async () => {
    setMe(["tenant.settings.manage", "consent.read"]);
    stub.routes["GET /bff/api/v1/consent-purposes"] = () => Response.json(list());
    const user = userEvent.setup();
    renderWithIntl(<PurposesScreen />);
    await user.click(await screen.findByRole("button", { name: cm.purposes.edit }));
    const dialog = screen.getByRole("dialog", { name: cm.purposes.editTitle });
    expect(
      within(dialog).getAllByText(
        "This makes version 3 of the form; earlier answers stay with version 2.",
      ).length,
    ).toBeGreaterThan(0);
  });

  it("a purpose nobody answered yet carries no version warning", async () => {
    setMe(["tenant.settings.manage"]);
    stub.routes["GET /bff/api/v1/consent-purposes"] = () =>
      Response.json(list({ data: [purpose({ current_version_answered: false, answered: 0 })] }));
    const user = userEvent.setup();
    renderWithIntl(<PurposesScreen />);
    await user.click(await screen.findByRole("button", { name: cm.purposes.edit }));
    const dialog = screen.getByRole("dialog", { name: cm.purposes.editTitle });
    expect(within(dialog).queryByText(cm.purposes.versionWarningTitle)).toBeNull();
  });

  it("staff who record answers see purposes read-only", async () => {
    setMe(["consent.record"]);
    stub.routes["GET /bff/api/v1/consent-purposes"] = () => Response.json(list());
    renderWithIntl(<PurposesScreen />);
    expect(await screen.findByText(cm.purposes.readOnlyTitle)).toBeInTheDocument();
    expect(await screen.findAllByText("Photos on the school website")).not.toHaveLength(0);
    expect(screen.queryByRole("button", { name: cm.purposes.add })).toBeNull();
    expect(screen.queryByRole("button", { name: cm.purposes.edit })).toBeNull();
  });

  it("without a consent permission nothing is loaded", async () => {
    setMe(["student.read_basic"]);
    renderWithIntl(<PurposesScreen />);
    expect(await screen.findByText(cm.purposes.noPermissionTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/consent-purposes")).toEqual([]);
  });

  it("428: using a suggestion asks to confirm it's you and sends the same request again", async () => {
    setMe(["tenant.settings.manage"]);
    stub.routes["GET /bff/api/v1/consent-purposes"] = () => Response.json(list());
    let attempts = 0;
    stub.routes["POST /bff/api/v1/consent-purposes"] = () => {
      attempts += 1;
      return attempts === 1
        ? problem(428, "step_up_required", { step_up_url: "/bff/auth/step-up?next=%2F" })
        : Response.json(purpose({ id: "0192f3a4-0000-7000-8000-00000000c0a2" }), { status: 201 });
    };
    const prompt = vi.fn(async () => true);
    unregister = registerStepUpHandler("staff", prompt);
    const user = userEvent.setup();
    renderWithIntl(<PurposesScreen />);
    await user.click(
      await screen.findByRole("button", {
        name: `${cm.purposes.enable}: The class WhatsApp group`,
      }),
    );
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/consent-purposes")).toHaveLength(2));
    expect(prompt).toHaveBeenCalledTimes(1);
    const [first, second] = stub.callsTo("POST /bff/api/v1/consent-purposes");
    expect(second?.headers.get("idempotency-key")).toBe(first?.headers.get("idempotency-key"));
    expect(JSON.parse(second?.body ?? "{}")).toEqual({ suggested_key: "whatsapp_group" });
  });
});

describe("US-2603 consent register summary", () => {
  it("shows agreed counts per purpose and asks for a section before printing", async () => {
    setMe(["consent.read"]);
    stub.routes["GET /bff/api/v1/consent-purposes"] = () => Response.json(list());
    stub.routes["GET /bff/api/v1/consents/summary"] = () =>
      Response.json({
        purposes: [
          {
            purpose_id: PURPOSE,
            key: "website_photos",
            label_en: "Photos on the school website",
            totals: {
              total: 1284,
              given: 642,
              refused: 300,
              pending: 100,
              withdrawn: 2,
              not_answered: 240,
            },
            sections: [
              {
                section_id: SECTION,
                class_section: "9-A (Telugu medium)",
                counts: {
                  total: 40,
                  given: 20,
                  refused: 10,
                  pending: 5,
                  withdrawn: 0,
                  not_answered: 5,
                },
              },
            ],
          },
        ],
      });
    renderWithIntl(<ConsentsScreen />);
    expect(await screen.findAllByText("642 of 1,284 agreed")).not.toHaveLength(0);
    expect(screen.getByText(cm.chooseSectionToPrint)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: new RegExp(cm.printForms) })).toBeNull();
  });

  it("explains when there are no purposes yet", async () => {
    setMe(["consent.read", "tenant.settings.manage"]);
    stub.routes["GET /bff/api/v1/consent-purposes"] = () =>
      Response.json(list({ data: [], suggestions: [] }));
    stub.routes["GET /bff/api/v1/consents/summary"] = () => Response.json({ purposes: [] });
    renderWithIntl(<ConsentsScreen />);
    expect(await screen.findByText(cm.noPurposesTitle)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: cm.setUpPurposes })).toHaveAttribute(
      "href",
      expect.stringContaining("/settings/consent-purposes"),
    );
  });

  it("explains when the person has no consent permission", async () => {
    setMe(["student.read_basic"]);
    renderWithIntl(<ConsentsScreen />);
    expect(await screen.findByText(cm.noPermissionTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/consents/summary")).toEqual([]);
  });
});
