import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setSheetSaverForTesting } from "@/features/sheets/download";
import { permissionsFrom } from "@/features/students/me";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { ATTRIBUTES, ID, importBatch } from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { ImportDetailView } from "./ImportDetail";
import { ImportsView } from "./ImportsScreen";
import type { ImportPreset } from "./presets";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/imports",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/** Import template library and the ERP refresh (US-403, US-204; FR-IMP-030..033). */

const im = messages.en.imports;
const ready = <T,>(data: T) => ({ status: "ready" as const, data });
const perms = permissionsFrom(["import.run", "import.commit", "document.upload"]);
let stub: BffStub;

const PRESETS: ImportPreset[] = [
  {
    key: "schoolos-blank",
    version: 1,
    label_en: "Blank SchoolOS template",
    label_te: "",
    description_en: "One row per student.",
    import_source: "admission_register",
    template: true,
    verified: true,
    source: [],
    columns: [{ header: "Admission number", target: "admission_no", aliases: [], note: "" }],
  },
  {
    key: "erp-student-export",
    version: 1,
    label_en: "Student export from your current ERP",
    label_te: "",
    description_en: "Refresh SchoolOS from your other school software.",
    import_source: "manual_entry",
    template: true,
    verified: false,
    source: [],
    columns: [{ header: "Admission No", target: "admission_no", aliases: [], note: "" }],
  },
];

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/import-presets"] = () => Response.json(PRESETS);
});
afterEach(() => {
  setSheetSaverForTesting(undefined);
  uninstallBffStub();
});

describe("US-403: the template library", () => {
  it("lists the presets, marks unconfirmed formats and downloads a header-only template", async () => {
    const saved: string[] = [];
    setSheetSaverForTesting((_blob, name) => saved.push(name));
    stub.routes["GET /bff/api/v1/import-presets/template"] = (_request, url) =>
      new Response("xlsx", {
        headers: {
          "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "content-disposition": `attachment; filename="schoolos-${url.searchParams.get("preset")}-v1.xlsx"`,
        },
      });
    const user = userEvent.setup();
    renderWithIntl(
      <ImportsView
        imports={ready([])}
        templates={ready([])}
        permissions={perms}
        page={1}
        onStarted={vi.fn()}
      />,
    );
    const library = await screen.findByRole("list", { name: im.library.title });
    expect(within(library).getAllByRole("listitem")).toHaveLength(2);
    expect(within(library).getByText(im.library.verified)).toBeInTheDocument();
    expect(within(library).getByText(im.library.unverified)).toBeInTheDocument();
    await user.click(
      within(library).getByRole("button", {
        name: im.library.downloadFor.replace("{name}", "Blank SchoolOS template"),
      }),
    );
    await waitFor(() => expect(saved).toEqual(["schoolos-schoolos-blank-v1.xlsx"]));
    const call = stub.callsTo("GET /bff/api/v1/import-presets/template")[0];
    expect(call?.url.searchParams.get("preset")).toBe("schoolos-blank");
  });

  it("is not shown to someone who cannot import", () => {
    renderWithIntl(
      <ImportsView
        imports={ready([])}
        templates={ready([])}
        permissions={permissionsFrom([])}
        page={1}
        onStarted={vi.fn()}
      />,
    );
    expect(screen.queryByRole("list", { name: im.library.title })).toBeNull();
    expect(stub.callsTo("GET /bff/api/v1/import-presets")).toHaveLength(0);
  });
});

describe("US-403 AC3: a preset fills in the mapping step without saving", () => {
  it("pre-selects the preset's fields and leaves the rest", async () => {
    stub.routes[`GET /bff/api/v1/imports/${ID.import}/preset-mapping`] = () =>
      Response.json({
        preset: "erp-student-export",
        import_source: "manual_entry",
        source_matches: false,
        columns: [
          { index: 0, header: "Adm No", target: "admission_no" },
          { index: 1, header: "విద్యార్థి పేరు", target: null },
          { index: 2, header: "DOB", target: null },
          { index: 3, header: "Remarks", target: "full_name" },
        ],
        missing: ["Father Name"],
      });
    const user = userEvent.setup();
    renderWithIntl(
      <ImportDetailView
        batch={ready(importBatch())}
        attributes={ready(ATTRIBUTES)}
        permissions={perms}
        initialPreset="erp-student-export"
      />,
    );
    const picker = await screen.findByLabelText(im.presets.label);
    expect(picker).toHaveValue("erp-student-export");
    await user.click(screen.getByRole("button", { name: im.presets.apply }));
    const table = screen.getByRole("list", { name: im.mapping.tableLabel });
    await waitFor(() =>
      expect(
        within(table).getByLabelText(im.mapping.targetFor.replace("{header}", "Remarks")),
      ).toHaveValue("full_name"),
    );
    // Columns the preset does not know keep their suggestion.
    expect(
      within(table).getByLabelText(im.mapping.targetFor.replace("{header}", "DOB")),
    ).toHaveValue("dob");
    expect(screen.getByText(/Not in your file: Father Name/)).toBeInTheDocument();
    expect(screen.getByText(im.presets.otherSource)).toBeInTheDocument();
    // Nothing is saved until the rows are checked (PUT /mapping happens on submit only).
    expect(stub.callsTo(`PUT /bff/api/v1/imports/${ID.import}/mapping`)).toHaveLength(0);
  });
});

describe("US-204: refresh from your ERP export", () => {
  it("offers the refresh as office records only when the school runs alongside its ERP", () => {
    const { unmount } = renderWithIntl(
      <ImportsView
        imports={ready([])}
        templates={ready([])}
        permissions={perms}
        page={1}
        onStarted={vi.fn()}
        alongside={{ erpName: "Synthetic ERP" }}
      />,
    );
    const title = im.refresh.titleNamed.replace("{name}", "Synthetic ERP");
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    expect(screen.getByText(im.refresh.registerKept)).toBeInTheDocument();
    const sources = screen.getAllByLabelText(im.upload.source);
    expect(sources[0]).toHaveValue("manual_entry");
    expect(sources[1]).toHaveValue("admission_register");
    unmount();
    renderWithIntl(
      <ImportsView
        imports={ready([])}
        templates={ready([])}
        permissions={perms}
        page={1}
        onStarted={vi.fn()}
      />,
    );
    expect(screen.queryByRole("heading", { name: im.refresh.title })).toBeNull();
  });
});
