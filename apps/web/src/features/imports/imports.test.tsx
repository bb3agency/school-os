import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { permissionsFrom } from "@/features/students/me";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import {
  ALL_RECORD_PERMISSIONS,
  ATTRIBUTES,
  ID,
  NOW,
  importBatch,
  importRow,
} from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { ImportDetailView, duplicateTargets, initialTarget } from "./ImportDetail";
import {
  ImportsView,
  UploadSpreadsheet,
  checkSpreadsheet,
  setNotReadyRetryForTesting,
} from "./ImportsScreen";
import { IMPORT_BUSY } from "./types";
import { setStoragePostForTesting, titleOf } from "./upload";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/imports",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/** Spreadsheet imports (US-401, FR-IMP-001..007). Synthetic data only. */

const im = messages.en.imports;
const ready = <T,>(data: T) => ({ status: "ready" as const, data });
let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  setNotReadyRetryForTesting(1);
});
afterEach(() => {
  setStoragePostForTesting(undefined);
  uninstallBffStub();
});

function file(name: string, size = 1024, type = "") {
  const blob = new File(["x"], name, { type });
  Object.defineProperty(blob, "size", { value: size });
  return blob;
}

describe("FR-IMP-001: which files can be imported", () => {
  it("takes XLSX or CSV up to 10 MB", () => {
    expect(checkSpreadsheet(undefined)).toBe("fileMissing");
    expect(checkSpreadsheet(file("class9.xlsx"))).toBeNull();
    expect(checkSpreadsheet(file("CLASS9.CSV"))).toBeNull();
    expect(checkSpreadsheet(file("class9.pdf"))).toBe("fileType");
    expect(checkSpreadsheet(file("class9.xlsx", 11 * 1024 * 1024))).toBe("fileTooLarge");
    expect(titleOf("list\u0007.csv")).toBe("list .csv");
  });

  it("uploads to storage, waits for the virus check, then starts the import", async () => {
    stub.routes["POST /bff/api/v1/documents/uploads"] = () =>
      Response.json(
        {
          upload_id: ID.upload,
          url: "https://files.schoolos.example/upload",
          fields: { key: "t/x/staging" },
          expires_at: NOW,
          max_bytes: 1024,
          purpose: "import_file",
          document_id: null,
          batch_id: null,
        },
        { status: 201 },
      );
    stub.routes["POST /bff/api/v1/documents"] = () =>
      Response.json({ id: ID.doc }, { status: 202 });
    stub.routes[`GET /bff/api/v1/documents/${ID.doc}`] = () =>
      Response.json({ id: ID.doc, current_version: { status: "ready" } });
    let attempts = 0;
    stub.routes["POST /bff/api/v1/imports"] = () => {
      attempts += 1;
      return attempts === 1
        ? problem(409, "document_not_ready")
        : Response.json(importBatch(), { status: 202 });
    };
    const posted = vi.fn(async () => undefined);
    setStoragePostForTesting(posted);
    const started = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(<UploadSpreadsheet onStarted={started} />);

    await user.click(screen.getByRole("button", { name: im.upload.submit }));
    expect(screen.getByText(im.upload.fileMissing)).toBeInTheDocument();

    await user.upload(screen.getByLabelText(im.upload.file), file("class9.csv", 1024, "text/csv"));
    await user.selectOptions(screen.getByLabelText(im.upload.source), "udise_plus");
    await user.click(screen.getByRole("button", { name: im.upload.submit }));
    await waitFor(() => expect(started).toHaveBeenCalled());

    expect(posted).toHaveBeenCalledTimes(1);
    const upload = JSON.parse(stub.callsTo("POST /bff/api/v1/documents/uploads")[0]?.body ?? "{}");
    expect(upload).toMatchObject({ purpose: "import_file", content_type: "text/csv" });
    const imports = stub.callsTo("POST /bff/api/v1/imports");
    expect(imports).toHaveLength(2);
    // The retry reuses the same Idempotency-Key (one user intent).
    expect(imports[0]?.headers.get("idempotency-key")).toBe(
      imports[1]?.headers.get("idempotency-key"),
    );
    expect(JSON.parse(imports[1]?.body ?? "{}")).toEqual({
      document_id: ID.doc,
      source: "udise_plus",
      kind: "spreadsheet",
    });
  });

  it("explains a blocked file in plain language", async () => {
    stub.routes["POST /bff/api/v1/documents/uploads"] = () =>
      Response.json({ upload_id: ID.upload, url: "https://files.schoolos.example/u", fields: {} });
    stub.routes["POST /bff/api/v1/documents"] = () => Response.json({ id: ID.doc });
    stub.routes[`GET /bff/api/v1/documents/${ID.doc}`] = () =>
      Response.json({ id: ID.doc, current_version: { status: "quarantined" } });
    setStoragePostForTesting(async () => undefined);
    const user = userEvent.setup();
    renderWithIntl(<UploadSpreadsheet onStarted={vi.fn()} />, "te");
    await user.upload(screen.getByLabelText(messages.te.imports.upload.file), file("class9.xlsx"));
    await user.click(screen.getByRole("button", { name: messages.te.imports.upload.submit }));
    expect(
      await screen.findByText(messages.te.imports.errors.quarantined.title),
    ).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/imports")).toHaveLength(0);
  });
});

describe("US-401: the imports list", () => {
  it("hides the upload without import.run and document.upload", () => {
    renderWithIntl(
      <ImportsView
        imports={ready([])}
        templates={ready([])}
        permissions={permissionsFrom(["import.run"])}
        page={1}
        onStarted={vi.fn()}
      />,
    );
    expect(screen.queryByRole("heading", { name: im.list.uploadTitle })).toBeNull();
    expect(screen.getByText(im.list.emptyTitle)).toBeInTheDocument();
  });

  it("links each import and shows rows with errors", () => {
    renderWithIntl(
      <ImportsView
        imports={ready([
          {
            id: ID.import,
            source: "admission_register",
            status: "validated",
            row_count: 120,
            error_count: 4,
            created_by: ID.user,
            created_at: NOW,
            committed_at: null,
            reverted_at: null,
          },
        ])}
        templates={ready([])}
        permissions={permissionsFrom(ALL_RECORD_PERMISSIONS)}
        page={1}
        onStarted={vi.fn()}
      />,
    );
    expect(screen.getByRole("heading", { name: im.list.uploadTitle })).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /27\/09\/2026/ });
    expect(link).toHaveAttribute("href", `/en/imports/${ID.import}`);
    expect(screen.getByText(im.status.validated)).toBeInTheDocument();
  });
});

describe("US-401 AC1..AC3: one import", () => {
  const perms = permissionsFrom(ALL_RECORD_PERMISSIONS);

  it("links to the sheet between checking and adding, not before the file was read (AC5)", () => {
    const view = renderWithIntl(
      <ImportDetailView
        batch={ready(importBatch({ status: "validated" }))}
        attributes={ready(ATTRIBUTES)}
        permissions={perms}
      />,
    );
    const link = screen.getByRole("link", { name: messages.en.sheets.import.open });
    expect(link).toHaveAttribute("href", `/en/imports/${ID.import}/sheet`);
    expect(screen.getByText(messages.en.sheets.import.openDescriptionEdit)).toBeInTheDocument();
    view.unmount();
    renderWithIntl(
      <ImportDetailView
        batch={ready(importBatch({ status: "parsing" }))}
        attributes={ready(ATTRIBUTES)}
        permissions={perms}
      />,
    );
    expect(screen.queryByRole("link", { name: messages.en.sheets.import.open })).toBeNull();
  });

  it("suggests columns from English and Telugu headings, checks duplicates, then maps and checks", async () => {
    expect(
      initialTarget({ index: 0, header: "A", suggested: "dob", score: 90, target: null }),
    ).toBe("dob");
    expect(
      initialTarget({ index: 0, header: "A", suggested: "dob", score: 90, target: "full_name" }),
    ).toBe("full_name");
    expect([...duplicateTargets({ 0: "dob", 1: "dob", 2: "ignore", 3: "ignore" })]).toEqual([0, 1]);

    stub.routes[`PUT /bff/api/v1/imports/${ID.import}/mapping`] = () =>
      Response.json(importBatch({ version: 4 }));
    stub.routes[`POST /bff/api/v1/imports/${ID.import}/validate`] = () =>
      Response.json(importBatch({ status: "validating", version: 5 }), { status: 202 });
    const user = userEvent.setup();
    renderWithIntl(
      <ImportDetailView
        batch={ready(importBatch())}
        attributes={ready(ATTRIBUTES)}
        permissions={perms}
      />,
    );
    // One card per spreadsheet column, in a list named like the old table.
    const table = screen.getByRole("list", { name: im.mapping.tableLabel });
    expect(within(table).getAllByRole("listitem")).toHaveLength(importBatch().columns.length);
    const telugu = within(table).getByLabelText(
      im.mapping.targetFor.replace("{header}", "విద్యార్థి పేరు"),
    );
    expect(telugu).toHaveValue("full_name");
    expect(within(table).getAllByText(im.mapping.suggested)).toHaveLength(3);
    // Aadhaar last 4 can only come from the Aadhaar source: not offered for a register import.
    expect(within(telugu).queryByRole("option", { name: "Aadhaar (last 4 digits)" })).toBeNull();

    const remarks = within(table).getByLabelText(
      im.mapping.targetFor.replace("{header}", "Remarks"),
    );
    await user.selectOptions(remarks, "dob");
    await user.click(screen.getByRole("button", { name: im.mapping.submit }));
    expect(screen.getAllByText(im.mapping.duplicate)).toHaveLength(2);
    expect(stub.callsTo(`PUT /bff/api/v1/imports/${ID.import}/mapping`)).toHaveLength(0);

    await user.selectOptions(remarks, "ignore");
    await user.click(screen.getByRole("button", { name: im.mapping.submit }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/imports/${ID.import}/validate`)).toHaveLength(1),
    );
    const put = stub.callsTo(`PUT /bff/api/v1/imports/${ID.import}/mapping`)[0];
    expect(put?.headers.get("if-match")).toBe('W/"3"');
    expect(JSON.parse(put?.body ?? "{}")).toEqual({
      columns: [
        { index: 0, target: "admission_no" },
        { index: 1, target: "full_name" },
        { index: 2, target: "dob" },
      ],
    });
  });

  it("requires the admission number column", async () => {
    const user = userEvent.setup();
    renderWithIntl(
      <ImportDetailView
        batch={ready(importBatch())}
        attributes={ready(ATTRIBUTES)}
        permissions={perms}
      />,
    );
    await user.selectOptions(
      screen.getByLabelText(im.mapping.targetFor.replace("{header}", "Adm No")),
      "ignore",
    );
    await user.click(screen.getByRole("button", { name: im.mapping.submit }));
    expect(screen.getByText(im.issues.admission_no_not_mapped)).toBeInTheDocument();
    expect(stub.callsTo(`PUT /bff/api/v1/imports/${ID.import}/mapping`)).toHaveLength(0);
  });

  it("shows row errors in plain language and adds only valid rows when asked", async () => {
    const batch = importBatch({
      status: "validated",
      row_count: 3,
      error_count: 1,
      stats: { rows: 3, valid: 2, errors: 1, warnings: 0, create: 2, update: 0 },
      can_commit: true,
    });
    stub.routes[`GET /bff/api/v1/imports/${ID.import}/rows`] = () =>
      page([
        importRow({
          row_no: 3,
          status: "error",
          action: null,
          errors: [
            { field: "dob", code: "invalid_date", message_key: "errors.invalid_date", ref: null },
            {
              field: "admission_no",
              code: "duplicate_in_file",
              message_key: "errors.duplicate_in_file",
              ref: "2",
            },
          ],
          sensitive: ["aadhaar_last4"],
        }),
      ]);
    stub.routes[`POST /bff/api/v1/imports/${ID.import}/commit`] = () =>
      Response.json(importBatch({ status: "committing" }), { status: 202 });
    const user = userEvent.setup();
    renderWithIntl(
      <ImportDetailView batch={ready(batch)} attributes={ready(ATTRIBUTES)} permissions={perms} />,
    );
    expect(await screen.findByText(im.issues.invalid_date)).toBeInTheDocument();
    expect(screen.getByText(im.issues.duplicate_in_file.replace("{row}", "2"))).toBeInTheDocument();
    expect(screen.getByText("Has 1 restricted value (hidden)")).toBeInTheDocument();
    // Opens on the rows with errors.
    expect(
      stub.callsTo(`GET /bff/api/v1/imports/${ID.import}/rows`)[0]?.url.searchParams.get("status"),
    ).toBe("error");

    await user.click(screen.getByRole("button", { name: im.commit.open }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: im.commit.submit }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/imports/${ID.import}/commit`)).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/imports/${ID.import}/commit`)[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ skip_error_rows: true });
    expect(call?.headers.get("idempotency-key")).toBeTruthy();
  });

  it("after adding, shows every row again (the error filter from before is reset)", async () => {
    const validated = importBatch({
      status: "validated",
      error_count: 1,
      stats: { rows: 3, valid: 2, errors: 1, warnings: 0, create: 2, update: 0 },
      can_commit: true,
    });
    const committed = importBatch({
      status: "committed",
      error_count: 1,
      committed_at: NOW,
      revert_deadline: NOW,
      can_revert: false,
      version: validated.version + 1,
    });
    stub.routes[`GET /bff/api/v1/imports/${ID.import}/rows`] = () => page([]);
    function Harness() {
      const [batch, setBatch] = useState(validated);
      return (
        <>
          <button type="button" onClick={() => setBatch(committed)}>
            finish
          </button>
          <ImportDetailView
            batch={ready(batch)}
            attributes={ready(ATTRIBUTES)}
            permissions={perms}
          />
        </>
      );
    }
    const user = userEvent.setup();
    renderWithIntl(<Harness />);
    const rowCalls = () => stub.callsTo(`GET /bff/api/v1/imports/${ID.import}/rows`);
    await waitFor(() => expect(rowCalls()).toHaveLength(1));
    expect(rowCalls()[0]?.url.searchParams.get("status")).toBe("error");

    await user.click(screen.getByRole("button", { name: "finish" }));
    await waitFor(() => expect(rowCalls().length).toBeGreaterThan(1));
    expect(rowCalls().at(-1)?.url.searchParams.has("status")).toBe(false);
  });

  it("does not offer adding without import.commit", () => {
    const batch = importBatch({ status: "validated", stats: { valid: 3 }, can_commit: true });
    stub.routes[`GET /bff/api/v1/imports/${ID.import}/rows`] = () => page([]);
    renderWithIntl(
      <ImportDetailView
        batch={ready(batch)}
        attributes={ready(ATTRIBUTES)}
        permissions={permissionsFrom(["import.run", "student.read_basic"])}
      />,
    );
    expect(screen.queryByRole("button", { name: im.commit.open })).toBeNull();
    expect(screen.getByText(im.detail.noCommitPermission)).toBeInTheDocument();
  });

  it("FR-IMP-005: offers undo until the deadline and explains a refusal", async () => {
    const batch = importBatch({
      status: "committed",
      committed_at: NOW,
      revert_deadline: "2026-09-28T05:30:00Z",
      can_revert: true,
      stats: { committed: 3, created: 3, updated: 0, skipped: 0 },
    });
    stub.routes[`GET /bff/api/v1/imports/${ID.import}/rows`] = () => page([]);
    stub.routes[`POST /bff/api/v1/imports/${ID.import}/revert`] = () =>
      problem(409, "import_has_dependents");
    const user = userEvent.setup();
    renderWithIntl(
      <ImportDetailView batch={ready(batch)} attributes={ready(ATTRIBUTES)} permissions={perms} />,
    );
    expect(
      screen.getByText(im.detail.revertUntil.replace("{deadline}", "28/09/2026 11:00")),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: im.revert.open }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: im.revert.submit }));
    expect(
      await within(dialog).findByText(im.errors.import_has_dependents.title),
    ).toBeInTheDocument();
  });

  it("after 24 hours says undo is closed and hides the button", () => {
    stub.routes[`GET /bff/api/v1/imports/${ID.import}/rows`] = () => page([]);
    renderWithIntl(
      <ImportDetailView
        batch={ready(importBatch({ status: "committed", revert_deadline: NOW, can_revert: false }))}
        attributes={ready(ATTRIBUTES)}
        permissions={perms}
      />,
    );
    expect(screen.getByText(im.detail.revertClosed)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: im.revert.open })).toBeNull();
  });

  it("explains why a file could not be read, and announces work in progress", () => {
    const { unmount } = renderWithIntl(
      <ImportDetailView
        batch={ready(
          importBatch({ status: "failed", error_code: "header_not_found", columns: [] }),
        )}
        attributes={ready(ATTRIBUTES)}
        permissions={perms}
      />,
    );
    expect(screen.getByText(im.failure.header_not_found)).toBeInTheDocument();
    unmount();
    renderWithIntl(
      <ImportDetailView
        batch={ready(importBatch({ status: "validating" }))}
        attributes={ready(ATTRIBUTES)}
        permissions={perms}
      />,
    );
    expect(
      within(screen.getAllByRole("status")[0] as HTMLElement).getByText(im.detail.busy.validating),
    ).toBeInTheDocument();
    expect(IMPORT_BUSY.has("validating")).toBe(true);
    expect(IMPORT_BUSY.has("validated")).toBe(false);
  });
});

describe("NFR-A11Y-001: import steps and the 24-hour undo (US-401, FR-IMP-005)", () => {
  const detailPerms = permissionsFrom(ALL_RECORD_PERMISSIONS);

  it("marks the step the import has reached, and finished steps in words", () => {
    stub.routes[`GET /bff/api/v1/imports/${ID.import}/rows`] = () => page([]);
    renderWithIntl(
      <ImportDetailView
        batch={ready(importBatch({ status: "validated" }))}
        attributes={ready(ATTRIBUTES)}
        permissions={detailPerms}
      />,
    );
    const steps = screen.getByRole("list", { name: im.steps.label });
    const items = within(steps).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(items[3]).toHaveAttribute("aria-current", "step");
    expect(items[3]).toHaveTextContent(im.steps.add);
    expect(items[0]).toHaveTextContent(`${im.steps.done} ${im.steps.upload}`);
    expect(items.filter((item) => item.hasAttribute("aria-current"))).toHaveLength(1);
  });

  it("shows how long is left to undo when the deadline is ahead", async () => {
    stub.routes[`GET /bff/api/v1/imports/${ID.import}/rows`] = () => page([]);
    const deadline = new Date(Date.now() + 5.5 * 3_600_000).toISOString();
    renderWithIntl(
      <ImportDetailView
        batch={ready(
          importBatch({ status: "committed", revert_deadline: deadline, can_revert: true }),
        )}
        attributes={ready(ATTRIBUTES)}
        permissions={detailPerms}
      />,
    );
    expect(await screen.findByText("About 5 hours left to undo")).toBeInTheDocument();
    expect(screen.getByText(im.detail.undoEyebrow)).toBeInTheDocument();
  });
});
