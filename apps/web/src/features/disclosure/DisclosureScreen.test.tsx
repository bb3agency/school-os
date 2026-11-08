import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { me } from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import type { DisclosureDraft, DisclosureFormat } from "./data";
import { DisclosureScreen } from "./DisclosureScreen";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/settings/disclosure",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/** CBSE disclosure screen (R3-1C; ADR-0051; FR-DISC-004, FR-DISC-008). Synthetic data only. */
const dm = messages.en.disclosure;
let stub: BffStub;

const FORMAT: DisclosureFormat = {
  key: "cbse-appendix-ix",
  version: 1,
  title: "Mandatory public disclosure",
  verified: true,
  sources: [],
  sections: [
    {
      key: "general",
      title: "General information",
      items: [
        {
          key: "affiliation_no",
          label: "Affiliation number",
          fill: "input",
          type: "text",
          required: true,
          contact: false,
          takes_documents: false,
          max_length: 40,
          max_value: null,
          max_rows: null,
        },
      ],
    },
  ],
};

const DRAFT: DisclosureDraft = {
  format_key: "cbse-appendix-ix",
  format_version: 1,
  inputs: { affiliation_no: "1130001" },
  documents: {},
  from_schoolos: {},
  missing: [],
  version: 3,
  updated_at: null,
  updated_by_name: null,
};

function routes() {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(["disclosure.edit", "disclosure.publish"]));
  stub.routes["GET /bff/api/v1/disclosure/format"] = () => Response.json(FORMAT);
  stub.routes["GET /bff/api/v1/disclosure/draft"] = () => Response.json(DRAFT);
  stub.routes["GET /bff/api/v1/disclosure/snapshots"] = () => Response.json([]);
  stub.routes["GET /bff/api/v1/disclosure/link"] = () => Response.json({ url: null });
}

beforeEach(() => {
  stub = installBffStub("staff");
  routes();
});
afterEach(() => uninstallBffStub());

describe("publishing never freezes a draft other than the one on screen (XP-01, FR-DISC-008)", () => {
  it("offers Publish only once the typed changes are saved", async () => {
    const user = userEvent.setup();
    renderWithIntl(<DisclosureScreen />);
    const field = await screen.findByLabelText(/Affiliation number/);
    const publish = screen.getByRole("button", { name: dm.publish.open });
    expect(publish).toBeEnabled();

    await user.clear(field);
    await user.type(field, "1130002");
    expect(screen.getByRole("button", { name: dm.publish.open })).toBeDisabled();
    expect(screen.getByText(dm.publish.saveFirst)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: messages.en.schoolSettings.discard }));
    expect(screen.getByRole("button", { name: dm.publish.open })).toBeEnabled();
    expect(screen.queryByText(dm.publish.saveFirst)).not.toBeInTheDocument();
  });
});

describe("the draft editor saves once per submit (XP-02, FR-DISC-004)", () => {
  it("two synchronous submits send one PUT", async () => {
    stub.routes["PUT /bff/api/v1/disclosure/draft"] = () =>
      Response.json({ ...DRAFT, version: 4, inputs: { affiliation_no: "1130002" } });
    const user = userEvent.setup();
    renderWithIntl(<DisclosureScreen />);
    const field = await screen.findByLabelText(/Affiliation number/);
    await user.clear(field);
    await user.type(field, "1130002");
    const form = field.closest("form");
    expect(form).not.toBeNull();
    act(() => {
      fireEvent.submit(form!);
      fireEvent.submit(form!);
    });
    await waitFor(() => expect(stub.callsTo("PUT /bff/api/v1/disclosure/draft")).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(stub.callsTo("PUT /bff/api/v1/disclosure/draft")).toHaveLength(1);
  });
});
