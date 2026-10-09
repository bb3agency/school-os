import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import type { components } from "@schoolos/api-client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import {
  ALL_RECORD_PERMISSIONS,
  ATTRIBUTES,
  ID,
  NOW,
  fakeAadhaar,
  student,
} from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { permissionsFrom } from "./me";
import { StudentDetailView } from "./StudentDetail";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => `/students/${ID.student}`,
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/**
 * APAAR ID and UDISE+ PEN on the student page (ADR-0037; FR-STU-013..015, US-301 AC4, US-303
 * AC2): shown with their source and verification state, recorded directly (not identity
 * fields), and the one field where a 12-digit number is accepted. Synthetic data only.
 */

type Schemas = components["schemas"];
const sm = messages.en.students;
const APAAR_VALUE_ID = "0192f3a4-0000-7000-8000-00000000c2a1";
const PEN_VALUE_ID = "0192f3a4-0000-7000-8000-00000000c2a2";
const APAAR = "123456789011"; // synthetic, fails Verhoeff
let stub: BffStub;

const attributes: Schemas["AttributeOut"][] = [
  ...ATTRIBUTES,
  {
    key: "udise_pen",
    data_type: "text",
    classification: "C2",
    is_identity: false,
    label_en: "UDISE+ PEN",
    label_te: "",
    sort_order: 92,
    allowed_sources: ["udise_plus", "manual_entry"],
    allowed_values: null,
    precedence: ["udise_plus", "manual_entry"],
    is_global: true,
  },
  {
    key: "apaar_id",
    data_type: "digits12",
    classification: "C2",
    is_identity: false,
    label_en: "APAAR ID",
    label_te: "",
    sort_order: 94,
    allowed_sources: ["udise_plus", "parent_form", "manual_entry"],
    allowed_values: null,
    precedence: ["udise_plus", "parent_form", "manual_entry"],
    is_global: true,
  },
];

function sourceValue(extra: Partial<Schemas["ValueOut"]>): Schemas["ValueOut"] {
  return {
    id: APAAR_VALUE_ID,
    attribute_key: "apaar_id",
    source: "udise_plus",
    value: APAAR,
    masked: false,
    verification_status: "unverified",
    verified_by: null,
    verified_at: null,
    recorded_by: ID.user,
    recorded_at: NOW,
    evidence_document_id: null,
    import_batch_id: null,
    change_request_id: null,
    superseded_by: null,
    current: true,
    ...extra,
  };
}

function withIds(verified: boolean): Schemas["StudentOut"] {
  const base = student();
  return {
    ...base,
    canonical: {
      ...base.canonical,
      apaar_id: {
        value: APAAR,
        source: "udise_plus",
        verified,
        provisional: !verified,
        masked: false,
        conflicts: [],
      },
      udise_pen: {
        value: "21345678901",
        source: "udise_plus",
        verified: true,
        provisional: false,
        masked: false,
        conflicts: [],
      },
    },
    values: {
      ...base.values,
      apaar_id: [sourceValue({ verification_status: verified ? "verified" : "unverified" })],
      udise_pen: [
        sourceValue({
          id: PEN_VALUE_ID,
          attribute_key: "udise_pen",
          value: "21345678901",
          verification_status: "verified",
        }),
      ],
    },
  };
}

const ready = <T,>(data: T) => ({ status: "ready" as const, data });

function renderDetail(data: Schemas["StudentOut"], permissions = ALL_RECORD_PERMISSIONS) {
  return renderWithIntl(
    <StudentDetailView
      student={ready(data)}
      attributes={ready(attributes)}
      guardians={ready([])}
      permissions={permissionsFrom(permissions)}
    />,
  );
}

function row(label: string): HTMLElement {
  const table = screen.getByRole("table", { name: sm.detail.valuesTable });
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const header = within(table).getByRole("rowheader", { name: new RegExp(`^${escaped}`) });
  const tr = header.closest("tr");
  if (!tr) throw new Error("no row");
  return tr;
}

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/attributes"] = () => Response.json(attributes);
});
afterEach(uninstallBffStub);

describe("FR-STU-013 / US-301 AC4: APAAR ID and PEN with source and verification", () => {
  it("shows the APAAR ID in full with its source, and says it does not count until verified", () => {
    renderDetail(withIds(false));
    const apaar = row("APAAR ID");
    expect(within(apaar).getAllByText(APAAR).length).toBeGreaterThan(0);
    expect(within(apaar).getAllByText(sm.sources.udise_plus).length).toBeGreaterThan(0);
    expect(within(apaar).getByText(sm.detail.apaarNotVerified)).toBeInTheDocument();
    expect(
      within(apaar).getByRole("button", {
        name: `${sm.detail.markVerified}: APAAR ID, ${sm.sources.udise_plus}`,
      }),
    ).toBeInTheDocument();
    // Not an identity field: changed directly, never through a correction request.
    expect(
      within(apaar).queryByRole("link", { name: new RegExp(sm.edit.requestChange) }),
    ).toBeNull();
    expect(
      within(apaar).getByRole("button", { name: `${sm.edit.change}: APAAR ID` }),
    ).toBeInTheDocument();
    const pen = row("UDISE+ PEN");
    expect(within(pen).getAllByText("21345678901").length).toBeGreaterThan(0);
  });

  it("drops the warning once the APAAR ID is verified", () => {
    renderDetail(withIds(true));
    expect(within(row("APAAR ID")).queryByText(sm.detail.apaarNotVerified)).toBeNull();
  });
});

describe("FR-STU-015 / US-303 AC2: the APAAR ID is the one 12-digit field", () => {
  it("accepts a 12-digit APAAR ID even when it passes Verhoeff and sends 12 digits", async () => {
    const posted: unknown[] = [];
    stub.routes[`POST /bff/api/v1/students/${ID.student}/values`] = async (request) => {
      posted.push(await request.json());
      return Response.json(
        {
          id: APAAR_VALUE_ID,
          student_id: ID.student,
          attribute_key: "apaar_id",
          source: "parent_form",
          superseded: null,
          student_version: 5,
        },
        { status: 201 },
      );
    };
    const verhoeff = fakeAadhaar("56781234567");
    const user = userEvent.setup();
    renderDetail(withIds(false));
    await user.click(screen.getByRole("button", { name: `${sm.edit.change}: APAAR ID` }));
    const dialog = screen.getByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText(sm.record.source), "parent_form");
    const input = within(dialog).getByLabelText(sm.record.value);
    await user.type(input, `${verhoeff.slice(0, 4)} ${verhoeff.slice(4, 8)} ${verhoeff.slice(8)}`);
    expect(within(dialog).queryByText(sm.aadhaarNotAllowed)).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: sm.record.submit }));
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({
      attribute_key: "apaar_id",
      source: "parent_form",
      value: verhoeff,
    });
  });

  it("refuses anything that is not 12 digits, and still refuses 12 digits in other fields", async () => {
    const user = userEvent.setup();
    renderDetail(withIds(false));
    await user.click(screen.getByRole("button", { name: `${sm.edit.change}: APAAR ID` }));
    let dialog = screen.getByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText(sm.record.source), "udise_plus");
    await user.type(within(dialog).getByLabelText(sm.record.value), "12345678901");
    await user.click(within(dialog).getByRole("button", { name: sm.record.submit }));
    expect(await within(dialog).findByText(sm.record.digits12Invalid)).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: `${sm.edit.change}: UDISE+ PEN` }));
    dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText(sm.record.value), fakeAadhaar());
    expect(within(dialog).getAllByText(sm.aadhaarNotAllowed).length).toBeGreaterThan(0);
  });
});
