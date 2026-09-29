import type { components } from "@schoolos/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SchoolShell } from "@/components/shell/SchoolShell";
import type { Locale } from "@/i18n/routing";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { fakeAadhaar, ID, me } from "@/test/records-fixtures";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { structureRoutes, SECTION } from "@/test/school-fixtures";
import DocumentPage from "@/app/[locale]/(school)/documents/[documentId]/page";
import NewDocumentPage from "@/app/[locale]/(school)/documents/new/page";
import DocumentsPage from "@/app/[locale]/(school)/documents/page";
import {
  documentRefetchInterval,
  setDocumentDownloadOpenerForTesting,
  setDocumentPollDelayForTesting,
} from "./data";
import { DocumentDetailScreen } from "./DocumentDetailScreen";
import { DocumentsScreen } from "./DocumentsScreen";
import { parseDeletedNotice, parseDocumentListFilters } from "./filters";
import { NewDocumentScreen } from "./NewDocumentScreen";
import { documentEditSchema, documentPatchBody } from "./forms";
import { purposeFor, versionBadge, versionReason } from "./types";
import { checkFile, contentTypeFor, setDocumentStorageSendForTesting } from "./upload";

const push = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/documents",
    useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
    notFound: () => {
      throw new Error("NEXT_NOT_FOUND");
    },
  };
});

type Schemas = components["schemas"];

const READ = "document.read";
const UPLOAD = "document.upload";
const MANAGE = "document.manage_acl";
const SENSITIVE = "student.read_sensitive";
const READ_BASIC = "student.read_basic";
const DOC = ID.doc;
const V1 = "0192f3a4-0000-7000-8000-00000000d101";
const V2 = "0192f3a4-0000-7000-8000-00000000d102";
const OTHER_USER = "0192f3a4-0000-7000-8000-00000000c599";

function version(overrides: Partial<Schemas["VersionOut"]> = {}): Schemas["VersionOut"] {
  return {
    id: V1,
    version_no: 1,
    mime_type: "application/pdf",
    size_bytes: 204_800,
    status: "ready",
    error: null,
    created_at: "2026-09-20T05:00:00Z",
    uploaded_by: null,
    uploaded_by_me: false,
    ...overrides,
  };
}

/** One synthetic document (a holiday circular). */
function detail(overrides: Partial<Schemas["DocumentDetail"]> = {}): Schemas["DocumentDetail"] {
  const versions = overrides.versions ?? [version()];
  return {
    id: DOC,
    purpose: "circular",
    doc_type: "circular",
    title: "Dasara holidays circular 2026",
    issuer: "Synthetic school",
    issued_on: "2026-09-15",
    academic_year_id: null,
    language: "en",
    sensitivity: "C1",
    status: "active",
    current_version: versions.at(-1) ?? null,
    acl: [],
    created_by: ID.user,
    uploaded_by: null,
    uploaded_by_me: false,
    created_at: "2026-09-20T05:00:00Z",
    updated_at: "2026-09-21T05:00:00Z",
    version: 3,
    allowed_doc_types: [
      "circular",
      "policy",
      "minutes",
      "certificate",
      "letter",
      "form",
      "report",
      "other",
    ],
    ...overrides,
    versions,
  };
}

function row(overrides: Partial<Schemas["DocumentDetail"]> = {}): Schemas["DocumentOut"] {
  const full: Partial<Schemas["DocumentDetail"]> = detail(overrides);
  delete full.versions;
  delete full.allowed_doc_types;
  return full as Schemas["DocumentOut"];
}

let stub: BffStub;

function setMe(permissions: string[], userId: string = ID.user) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json({ ...me(permissions), user_id: userId });
}

function body(key: string, index = 0): Record<string, unknown> {
  return JSON.parse(stub.callsTo(key)[index]?.body ?? "{}") as Record<string, unknown>;
}

beforeEach(() => {
  push.mockReset();
  stub = installBffStub("staff");
  Object.assign(stub.routes, structureRoutes());
  setDocumentPollDelayForTesting(() => 10);
});

afterEach(() => {
  setDocumentPollDelayForTesting(null);
  setDocumentDownloadOpenerForTesting(null);
  setDocumentStorageSendForTesting(undefined);
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("document helpers (FR-DOC-001, FR-DOC-008)", () => {
  it("keeps only known codes from the URL", () => {
    expect(
      parseDocumentListFilters({ purpose: "circular", doc_type: "minutes", status: "archived" }),
    ).toEqual({ purpose: "circular", docType: "minutes", status: "archived" });
    expect(
      parseDocumentListFilters({ purpose: "<script>", doc_type: ["x"], status: "deleted" }),
    ).toEqual({ purpose: null, docType: null, status: null });
    expect(parseDeletedNotice({ deleted: "1" })).toBe(true);
    expect(parseDeletedNotice({ deleted: DOC })).toBe(false);
  });

  it("explains each blocked state by its code", () => {
    expect(versionReason(version())).toBeNull();
    expect(versionReason(version({ status: "scanning" }))).toBe("busy");
    expect(versionReason(version({ status: "quarantined", error: "malware_detected" }))).toBe(
      "malware_detected",
    );
    expect(versionReason(version({ status: "quarantined", error: "aadhaar_detected" }))).toBe(
      "aadhaar_detected",
    );
    expect(versionReason(version({ status: "quarantined", error: "something_new" }))).toBe(
      "quarantined_other",
    );
    expect(versionReason(version({ status: "failed", error: null }))).toBe("failed_other");
    expect(versionBadge(version({ status: "quarantined", error: "aadhaar_unredactable" }))).toBe(
      "withheld",
    );
    expect(versionBadge(version({ status: "quarantined", error: "aadhaar_redacted" }))).toBe(
      "replaced",
    );
    expect(versionBadge(version({ status: "quarantined", error: "malware_detected" }))).toBe(
      "quarantined",
    );
  });

  it("checks the file before anything is sent (types by name, 25 MB)", () => {
    const pdf = new File(["%PDF"], "circular.pdf", { type: "application/pdf" });
    const docx = new File(["PK"], "minutes.DOCX", { type: "" });
    expect(checkFile(pdf, "circular")).toBeNull();
    expect(contentTypeFor(docx, "other")).toBe(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    );
    expect(checkFile(docx, "evidence")).toBe("fileType");
    expect(checkFile(new File(["x"], "run.exe"), "other")).toBe("fileType");
    expect(checkFile(new File([], "empty.pdf"), "other")).toBe("fileEmpty");
    expect(checkFile(null, "other")).toBe("fileRequired");
    const big = new File(["x"], "big.pdf");
    Object.defineProperty(big, "size", { value: 25 * 1024 * 1024 + 1 });
    expect(checkFile(big, "other")).toBe("fileTooLarge");
    expect(purposeFor("policy")).toBe("policy");
    expect(purposeFor("minutes")).toBe("other");
  });

  it("polls only while a current version is being checked", () => {
    expect(documentRefetchInterval([{ current_version: { status: "queued" } }], 0)).toBe(10);
    expect(documentRefetchInterval([{ current_version: { status: "ready" } }], 0)).toBe(false);
    expect(documentRefetchInterval([{ current_version: null }], 0)).toBe(false);
    expect(documentRefetchInterval([{ current_version: { status: "scanning" } }], 0, true)).toBe(
      false,
    );
  });
});

describe("documents list (US-701, FR-DOC-005..008)", () => {
  it("says so without document.read and asks the API nothing", async () => {
    setMe([READ_BASIC]);
    renderWithIntl(<DocumentsScreen filters={parseDocumentListFilters({})} />);
    expect(await screen.findByText("You can't see documents")).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/documents")).toHaveLength(0);
  });

  it("lists documents with their check status and sends the filters as codes", async () => {
    setMe([READ]);
    stub.routes["GET /bff/api/v1/documents"] = () =>
      page([
        row(),
        row({
          id: "0192f3a4-0000-7000-8000-00000000c709",
          title: "Staff meeting minutes",
          doc_type: "minutes",
          purpose: "other",
          acl: [{ principal_type: "role", principal_ref: "principal" }],
          versions: [version({ status: "quarantined", error: "malware_detected" })],
        }),
      ]);
    renderWithIntl(<DocumentsScreen filters={parseDocumentListFilters({ purpose: "circular" })} />);
    expect(
      await screen.findByRole("link", { name: "Dasara holidays circular 2026" }),
    ).toHaveAttribute("href", `/en/documents/${DOC}`);
    const blocked = screen.getAllByRole("row")[2];
    expect(blocked && within(blocked).getByText("Blocked")).toBeInTheDocument();
    expect(blocked && within(blocked).getByText("Only some staff")).toBeInTheDocument();
    // File-type chip and the size in mono from the current version (NFR-A11Y-001: text, not icons).
    const first = screen.getAllByRole("row")[1];
    expect(first && within(first).getByText("PDF")).toBeInTheDocument();
    expect(first && within(first).getByText(/v1/)).toHaveClass("font-mono");
    const calls = stub.callsTo("GET /bff/api/v1/documents");
    expect(calls[0]?.url.searchParams.get("purpose")).toBe("circular");
    expect(calls[0]?.url.searchParams.has("doc_type")).toBe(false);
    // Uploading needs document.upload.
    expect(screen.queryByRole("link", { name: "Upload a document" })).not.toBeInTheDocument();
  });

  it("lists archived documents only when the filter asks for them (FR-DOC-006)", async () => {
    setMe([READ]);
    stub.routes["GET /bff/api/v1/documents"] = () => page([row({ status: "archived" })]);
    const { unmount } = renderWithIntl(<DocumentsScreen filters={parseDocumentListFilters({})} />);
    const filter = await screen.findByLabelText(messages.en.documents.list.filterStatus);
    // No status sent: the API lists documents in use; the choice says so instead of "All".
    expect(filter).toHaveValue("");
    expect(
      within(filter)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual([messages.en.documents.list.statusInUse, messages.en.documents.list.statusArchived]);
    await waitFor(() => expect(stub.callsTo("GET /bff/api/v1/documents")).toHaveLength(1));
    expect(stub.callsTo("GET /bff/api/v1/documents")[0]?.url.searchParams.has("status")).toBe(
      false,
    );
    unmount();
    renderWithIntl(<DocumentsScreen filters={parseDocumentListFilters({ status: "archived" })} />);
    expect(
      await screen.findByRole("link", { name: "Dasara holidays circular 2026" }),
    ).toBeInTheDocument();
    const calls = stub.callsTo("GET /bff/api/v1/documents");
    expect(calls.at(-1)?.url.searchParams.get("status")).toBe("archived");
    expect(
      screen.getByRole("link", { name: messages.en.documents.list.clearFilters }),
    ).toBeInTheDocument();
  });

  it("offers upload to uploaders and confirms a delete", async () => {
    setMe([READ, UPLOAD]);
    stub.routes["GET /bff/api/v1/documents"] = () => page([]);
    renderWithIntl(<DocumentsScreen filters={parseDocumentListFilters({})} deleted />);
    expect(await screen.findByRole("link", { name: "Upload a document" })).toHaveAttribute(
      "href",
      "/en/documents/new",
    );
    expect(screen.getByText("Document deleted")).toBeInTheDocument();
    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
  });
});

describe("document detail (US-701 AC3..AC4, FR-DOC-002, FR-DOC-004, FR-DOC-006)", () => {
  it("downloads a ready version through a short-lived link that is used once", async () => {
    setMe([READ]);
    stub.routes[`GET /bff/api/v1/documents/${DOC}`] = () => Response.json(detail());
    const presigned = "https://files.schoolos.example/bucket/t/doc-v1?X-Amz-Signature=synthetic";
    stub.routes[`GET /bff/api/v1/documents/${DOC}/download-url`] = () =>
      Response.json({
        url: presigned,
        expires_at: "2026-09-27T05:35:00Z",
        version_no: 1,
        mime_type: "application/pdf",
        filename: "circular-0192f3a4-v1.pdf",
      });
    const opened: string[] = [];
    setDocumentDownloadOpenerForTesting((url) => opened.push(url));
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    await userEvent.click(await screen.findByRole("button", { name: "Download version 1" }));
    await waitFor(() => expect(opened).toEqual([presigned]));
    // Version history is a timeline: one entry per version, its state said in words.
    const history = screen.getByRole("list", { name: "Versions" });
    const entries = within(history).getAllByRole("listitem");
    expect(entries).toHaveLength(1);
    expect(entries[0]).toHaveTextContent(/Ready\s*Version 1/);
    const call = stub.callsTo(`GET /bff/api/v1/documents/${DOC}/download-url`)[0];
    expect(call?.url.searchParams.get("version")).toBe("1");
    // Read-only member: no ACL change, no new version, no delete.
    expect(screen.queryByRole("button", { name: "Change who can see it" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Upload a new version" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete document" })).toBeNull();
    expect(screen.getByText("Everyone in the school with document access.")).toBeInTheDocument();
    // The presigned link is not kept on the page.
    expect(document.body.innerHTML).not.toContain("X-Amz-Signature");
  });

  it("explains a virus-blocked and an Aadhaar-withheld version without showing any number", async () => {
    setMe([READ]);
    stub.routes[`GET /bff/api/v1/documents/${DOC}`] = () =>
      Response.json(
        detail({
          versions: [
            version(),
            version({
              id: V2,
              version_no: 2,
              status: "quarantined",
              error: "aadhaar_detected",
            }),
          ],
        }),
      );
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    expect(await screen.findByText("Version 2 cannot be opened")).toBeInTheDocument();
    const history = screen.getByRole("list", { name: "Versions" });
    const [newest, older] = within(history).getAllByRole("listitem");
    expect(newest).toHaveTextContent(/Version 2/);
    expect(older).toHaveTextContent(/Version 1/);
    expect(screen.getAllByText(/showed a full Aadhaar number/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Withheld").length).toBeGreaterThan(0);
    // The newest version that passed the check is still offered, and says why.
    expect(screen.getByRole("button", { name: "Download version 1" })).toBeInTheDocument();
    expect(screen.getByText(/this downloads version 1/)).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("aadhaar_detected");
    expect(text).not.toMatch(/\d{12}/);
    expect(text).not.toContain(fakeAadhaar());
  });

  it("says a file failed the virus check and how to fix it", async () => {
    setMe([READ]);
    stub.routes[`GET /bff/api/v1/documents/${DOC}`] = () =>
      Response.json(
        detail({ versions: [version({ status: "quarantined", error: "malware_detected" })] }),
      );
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    expect(await screen.findByText("Version 1 cannot be opened")).toBeInTheDocument();
    expect(screen.getAllByText(/found a threat in this file/).length).toBeGreaterThan(0);
    expect(screen.getByText("No version of this document can be opened yet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
  });

  it("polls while the virus check runs and shows the file once it is ready", async () => {
    setMe([READ]);
    let calls = 0;
    stub.routes[`GET /bff/api/v1/documents/${DOC}`] = () => {
      calls += 1;
      return Response.json(
        detail({ versions: [version({ status: calls === 1 ? "queued" : "ready" })] }),
      );
    };
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    expect(await screen.findByText("Version 1 is being checked")).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Download version 1" }, { timeout: 2000 }),
    ).toBeInTheDocument();
  });

  it("keeps restricted (C3) files closed for members without sensitive access", async () => {
    setMe([READ], OTHER_USER);
    stub.routes[`GET /bff/api/v1/documents/${DOC}`] = () =>
      Response.json(detail({ sensitivity: "C3" }));
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    expect(await screen.findByText("Restricted file")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
  });

  it("opens restricted files for the uploader", async () => {
    setMe([READ]);
    stub.routes[`GET /bff/api/v1/documents/${DOC}`] = () =>
      Response.json(detail({ sensitivity: "C3" }));
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    expect(await screen.findByRole("button", { name: "Download version 1" })).toBeInTheDocument();
  });

  it("says a document is missing (404 outside the member's scope)", async () => {
    setMe([READ, SENSITIVE]);
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    expect(await screen.findByText("We couldn't find this document")).toBeInTheDocument();
  });

  it("changes who can see it with If-Match and the chosen entries", async () => {
    setMe([READ, MANAGE, READ_BASIC]);
    stub.routes[`GET /bff/api/v1/documents/${DOC}`] = () => Response.json(detail());
    stub.routes[`PUT /bff/api/v1/documents/${DOC}/acl`] = () => Response.json(row());
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    await userEvent.click(await screen.findByRole("button", { name: "Change who can see it" }));
    const dialog = await screen.findByRole("dialog", { name: "Change who can see this document" });
    // "Only some staff" without a choice is refused before anything is sent.
    await userEvent.click(within(dialog).getByRole("radio", { name: "Only some staff" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(
      await within(dialog).findByText(/Choose at least one role, class, section or staff member/),
    ).toBeInTheDocument();
    expect(stub.callsTo(`PUT /bff/api/v1/documents/${DOC}/acl`)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Principal" }));
    await userEvent.click(await within(dialog).findByRole("checkbox", { name: "Class 9 · A" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(stub.callsTo(`PUT /bff/api/v1/documents/${DOC}/acl`)).toHaveLength(1),
    );
    const call = stub.callsTo(`PUT /bff/api/v1/documents/${DOC}/acl`)[0];
    expect(call?.headers.get("if-match")).toBe('W/"3"');
    expect(body(`PUT /bff/api/v1/documents/${DOC}/acl`)).toEqual({
      acl: [
        { principal_type: "role", principal_ref: "principal" },
        { principal_type: "section", principal_ref: SECTION },
      ],
    });
  });

  it("deletes only after the confirm dialog and returns to the list", async () => {
    setMe([READ, MANAGE]);
    stub.routes[`GET /bff/api/v1/documents/${DOC}`] = () => Response.json(detail());
    stub.routes[`DELETE /bff/api/v1/documents/${DOC}`] = () => new Response(null, { status: 204 });
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete document" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete this document?" });
    expect(stub.callsTo(`DELETE /bff/api/v1/documents/${DOC}`)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(stub.callsTo(`DELETE /bff/api/v1/documents/${DOC}`)).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Delete document" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/en/documents?deleted=1"));
    expect(stub.callsTo(`DELETE /bff/api/v1/documents/${DOC}`)).toHaveLength(1);
  });

  it("explains why evidence still in use cannot be deleted", async () => {
    setMe([READ, MANAGE]);
    stub.routes[`GET /bff/api/v1/documents/${DOC}`] = () => Response.json(detail());
    stub.routes[`DELETE /bff/api/v1/documents/${DOC}`] = () => problem(409, "document_in_use");
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete document" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete this document?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(await within(dialog).findByText("This document must be kept")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("uploads a new version straight to storage, then registers it", async () => {
    setMe([READ, UPLOAD]);
    stub.routes[`GET /bff/api/v1/documents/${DOC}`] = () => Response.json(detail());
    stub.routes["POST /bff/api/v1/documents/uploads"] = () =>
      Response.json(
        {
          upload_id: ID.upload,
          url: "https://files.schoolos.example/bucket",
          fields: { key: "t/uploads/x.pdf", policy: "synthetic" },
          expires_at: "2026-09-27T05:40:00Z",
          max_bytes: 5,
          purpose: "circular",
          document_id: DOC,
          batch_id: null,
        },
        { status: 201 },
      );
    const stored: string[] = [];
    setDocumentStorageSendForTesting(async (url, init) => {
      stored.push(`${init.method ?? "GET"} ${url}`);
      return new Response(null, { status: 204 });
    });
    stub.routes[`POST /bff/api/v1/documents/${DOC}/versions`] = () =>
      Response.json(row(), { status: 202 });
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    await userEvent.click(await screen.findByRole("button", { name: "Upload a new version" }));
    const dialog = await screen.findByRole("dialog", { name: "Upload a new version" });
    await userEvent.upload(
      within(dialog).getByLabelText("File"),
      new File(["%PDF"], "circular-v2.pdf", { type: "application/pdf" }),
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Upload" }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/documents/${DOC}/versions`)).toHaveLength(1),
    );
    expect(stored).toEqual(["POST https://files.schoolos.example/bucket"]);
    expect(body("POST /bff/api/v1/documents/uploads")).toEqual({
      filename: "circular-v2.pdf",
      content_type: "application/pdf",
      size_bytes: 4,
      purpose: "circular",
      document_id: DOC,
    });
    expect(body(`POST /bff/api/v1/documents/${DOC}/versions`)).toEqual({ upload_id: ID.upload });
    const keys = [
      stub.callsTo("POST /bff/api/v1/documents/uploads")[0]?.headers.get("idempotency-key"),
      stub.callsTo(`POST /bff/api/v1/documents/${DOC}/versions`)[0]?.headers.get("idempotency-key"),
    ];
    expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
    expect(keys[1]).toMatch(/^[0-9a-f-]{36}$/);
    expect(keys[0]).not.toBe(keys[1]);
  });
});

describe("document details, archive and uploader (FR-DOC-005, FR-DOC-006, US-701)", () => {
  const PATCH = `PATCH /bff/api/v1/documents/${DOC}`;
  const ARCHIVE = `POST /bff/api/v1/documents/${DOC}/archive`;
  const UNARCHIVE = `POST /bff/api/v1/documents/${DOC}/unarchive`;
  const copy = messages.en.documents;

  function serve(overrides: Partial<Schemas["DocumentDetail"]> = {}) {
    stub.routes[`GET /bff/api/v1/documents/${DOC}`] = () => Response.json(detail(overrides));
  }

  async function openEdit() {
    await userEvent.click(await screen.findByRole("button", { name: copy.edit.trigger }));
    return screen.findByRole("dialog", { name: copy.edit.title });
  }

  it("offers only the types the API lists for the document's purpose (allowed_doc_types)", async () => {
    setMe([READ, UPLOAD]);
    serve({
      purpose: "evidence",
      doc_type: "evidence",
      allowed_doc_types: ["evidence", "certificate", "letter"],
    });
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    const dialog = await openEdit();
    const type = within(dialog).getByLabelText(copy.new.docType);
    const options = within(type)
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value)
      .filter(Boolean);
    expect(options).toEqual(["evidence", "certificate", "letter"]);
  });

  it("offers only the current type when the API lists none", async () => {
    setMe([READ, UPLOAD]);
    serve({ purpose: "register_scan", doc_type: "register_scan", allowed_doc_types: [] });
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    const dialog = await openEdit();
    const type = within(dialog).getByLabelText(copy.new.docType);
    const options = within(type)
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value)
      .filter(Boolean);
    expect(options).toEqual(["register_scan"]);
  });

  it("builds a PATCH body with only what changed; emptied optional fields are null", () => {
    const current = detail();
    expect(
      documentPatchBody(
        current,
        documentEditSchema.parse({
          title: " Dasara holidays circular 2026 (revised) ",
          doc_type: "circular",
          language: "",
          issuer: "",
          issued_on: "2026-09-15",
        }),
      ),
    ).toEqual({ title: "Dasara holidays circular 2026 (revised)", language: null, issuer: null });
    expect(
      documentEditSchema.safeParse({
        title: `Circular ${fakeAadhaar()}`,
        doc_type: "circular",
        language: "",
        issuer: "",
        issued_on: "",
      }).success,
    ).toBe(false);
  });

  it("edits the details with If-Match and only the changed fields", async () => {
    setMe([READ, UPLOAD]);
    serve();
    stub.routes[PATCH] = () => Response.json(row({ title: "Dasara holidays 2026", version: 4 }));
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    const dialog = await openEdit();
    const title = within(dialog).getByLabelText(copy.new.titleField);
    expect(title).toHaveValue("Dasara holidays circular 2026");
    const type = within(dialog).getByLabelText(copy.new.docType);
    expect(within(type).queryByRole("option", { name: copy.docType.evidence })).toBeNull();
    await userEvent.clear(title);
    await userEvent.type(title, "Dasara holidays 2026");
    await userEvent.selectOptions(type, "letter");
    await userEvent.click(within(dialog).getByRole("button", { name: copy.edit.confirm }));
    await waitFor(() => expect(stub.callsTo(PATCH)).toHaveLength(1));
    expect(stub.callsTo(PATCH)[0]?.headers.get("if-match")).toBe('W/"3"');
    expect(body(PATCH)).toEqual({ title: "Dasara holidays 2026", doc_type: "letter" });
  });

  it("shows 422 doc_type_not_allowed_for_purpose on the type and 409 document_archived", async () => {
    setMe([READ, UPLOAD]);
    serve();
    let attempts = 0;
    stub.routes[PATCH] = () => {
      attempts += 1;
      return attempts === 1
        ? problem(422, "validation_error", {
            errors: [
              {
                field: "doc_type",
                code: "doc_type_not_allowed_for_purpose",
                message_key: "errors.doc_type_not_allowed_for_purpose",
              },
            ],
          })
        : problem(409, "document_archived");
    };
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    const dialog = await openEdit();
    await userEvent.selectOptions(within(dialog).getByLabelText(copy.new.docType), "minutes");
    await userEvent.click(within(dialog).getByRole("button", { name: copy.edit.confirm }));
    expect(
      await within(dialog).findByText(messages.en.errors.field.doc_type_not_allowed_for_purpose),
    ).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: copy.edit.confirm }));
    expect(
      await within(dialog).findByText(copy.errors.document_archived.title),
    ).toBeInTheDocument();
  });

  it("archives only after the confirm dialog, with If-Match (document.manage_acl)", async () => {
    setMe([READ, MANAGE]);
    serve();
    stub.routes[ARCHIVE] = () => Response.json(row({ status: "archived", version: 4 }));
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    await userEvent.click(await screen.findByRole("button", { name: copy.archive.trigger }));
    const dialog = await screen.findByRole("dialog", { name: copy.archive.title });
    expect(stub.callsTo(ARCHIVE)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: copy.archive.confirm }));
    await waitFor(() => expect(stub.callsTo(ARCHIVE)).toHaveLength(1));
    expect(stub.callsTo(ARCHIVE)[0]?.headers.get("if-match")).toBe('W/"3"');
  });

  it("an archived document says so, offers unarchive, and no edit or new version", async () => {
    setMe([READ, UPLOAD, MANAGE]);
    serve({ status: "archived" });
    stub.routes[UNARCHIVE] = () => Response.json(row({ version: 4 }));
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    expect(await screen.findByText(copy.detail.archivedTitle)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: copy.edit.trigger })).toBeNull();
    expect(screen.queryByRole("button", { name: copy.newVersion.trigger })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: copy.archive.unTrigger }));
    const dialog = await screen.findByRole("dialog", { name: copy.archive.unTitle });
    await userEvent.click(within(dialog).getByRole("button", { name: copy.archive.unConfirm }));
    await waitFor(() => expect(stub.callsTo(UNARCHIVE)).toHaveLength(1));
    expect(stub.callsTo(UNARCHIVE)[0]?.headers.get("if-match")).toBe('W/"3"');
  });

  it("offers no edit without document.upload and no archive without document.manage_acl", async () => {
    setMe([READ]);
    serve();
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    expect(await screen.findByText(copy.detail.aboutTitle)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: copy.edit.trigger })).toBeNull();
    expect(screen.queryByRole("button", { name: copy.archive.trigger })).toBeNull();
  });

  it("names the uploader from the API instead of 'another staff member'", async () => {
    setMe([READ], OTHER_USER);
    serve({
      uploaded_by: {
        membership_id: "0192f3a4-0000-7000-8000-0000000000e9",
        display_name: "Ravi Sample",
      },
    });
    const { unmount } = renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    expect(await screen.findByText("Ravi Sample")).toBeInTheDocument();
    unmount();
    serve({ uploaded_by: null, uploaded_by_me: true });
    const second = renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    expect(await screen.findByText(copy.detail.you)).toBeInTheDocument();
    second.unmount();
    serve({ uploaded_by: null, uploaded_by_me: false });
    renderWithIntl(<DocumentDetailScreen documentId={DOC} />);
    expect(await screen.findByText(copy.detail.formerMember)).toBeInTheDocument();
  });
});

describe("upload a document (US-701 AC1..AC2, FR-DOC-001, FR-DOC-005)", () => {
  function presign() {
    stub.routes["POST /bff/api/v1/documents/uploads"] = () =>
      Response.json(
        {
          upload_id: ID.upload,
          url: "https://files.schoolos.example/bucket",
          fields: { key: "t/uploads/x.docx", policy: "synthetic" },
          expires_at: "2026-09-27T05:40:00Z",
          max_bytes: 2,
          purpose: "other",
          document_id: null,
          batch_id: null,
        },
        { status: 201 },
      );
  }

  it("refuses without document.upload", async () => {
    setMe([READ]);
    renderWithIntl(<NewDocumentScreen />);
    expect(await screen.findByText("You can't upload documents")).toBeInTheDocument();
  });

  it("checks the form before sending anything", async () => {
    setMe([READ, UPLOAD]);
    renderWithIntl(<NewDocumentScreen />);
    await userEvent.click(await screen.findByRole("button", { name: "Upload a document" }));
    expect(await screen.findByText("Choose a file to upload.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Title/ })).toHaveAttribute("aria-invalid", "true");
    expect(stub.callsTo("POST /bff/api/v1/documents/uploads")).toHaveLength(0);
  });

  it("uploads, registers the details and who can see it, then opens the document", async () => {
    setMe([READ, UPLOAD, READ_BASIC]);
    presign();
    setDocumentStorageSendForTesting(async () => new Response(null, { status: 204 }));
    stub.routes["POST /bff/api/v1/documents"] = () => Response.json(row(), { status: 202 });
    renderWithIntl(<NewDocumentScreen />);
    await userEvent.upload(
      await screen.findByLabelText("File to upload"),
      new File(["PK"], "minutes.docx", { type: "" }),
    );
    await userEvent.type(screen.getByRole("textbox", { name: /Title/ }), "Staff meeting minutes");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Type" }), "minutes");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Language/ }), "te");
    await userEvent.click(screen.getByRole("radio", { name: /Personal details/ }));
    await userEvent.click(screen.getByRole("radio", { name: "Only some staff" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "Class 9" }));
    await userEvent.click(screen.getByRole("button", { name: "Upload a document" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/en/documents/${DOC}`));
    expect(body("POST /bff/api/v1/documents/uploads")).toEqual({
      filename: "minutes.docx",
      content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      size_bytes: 2,
      purpose: "other",
    });
    expect(body("POST /bff/api/v1/documents")).toEqual({
      upload_id: ID.upload,
      title: "Staff meeting minutes",
      doc_type: "minutes",
      sensitivity: "C2",
      language: "te",
      issuer: null,
      issued_on: null,
      acl: [{ principal_type: "class", principal_ref: "0192f3a4-0000-7000-8000-0000000000c9" }],
    });
  });

  it("asks to upload again when storage refused the file, with a fresh upload form", async () => {
    setMe([READ, UPLOAD]);
    presign();
    let attempts = 0;
    setDocumentStorageSendForTesting(async () => {
      attempts += 1;
      return new Response(null, { status: attempts === 1 ? 403 : 204 });
    });
    stub.routes["POST /bff/api/v1/documents"] = () => Response.json(row(), { status: 202 });
    renderWithIntl(<NewDocumentScreen />);
    await userEvent.upload(
      await screen.findByLabelText("File to upload"),
      new File(["%PDF"], "policy.pdf", { type: "application/pdf" }),
    );
    await userEvent.type(screen.getByRole("textbox", { name: /Title/ }), "Fee policy");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Type" }), "policy");
    await userEvent.click(screen.getByRole("button", { name: "Upload a document" }));
    expect(await screen.findByText(/The file could not be uploaded/)).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/documents")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Upload a document" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/en/documents/${DOC}`));
    const presigns = stub.callsTo("POST /bff/api/v1/documents/uploads");
    expect(presigns).toHaveLength(2);
    expect(presigns[0]?.headers.get("idempotency-key")).not.toBe(
      presigns[1]?.headers.get("idempotency-key"),
    );
    expect(body("POST /bff/api/v1/documents/uploads")).toMatchObject({ purpose: "policy" });
    expect(body("POST /bff/api/v1/documents")).toMatchObject({ acl: [], sensitivity: "C1" });
  });

  it("shows a scoped uploader's ACL problem on the visibility choice", async () => {
    setMe([READ, UPLOAD]);
    presign();
    setDocumentStorageSendForTesting(async () => new Response(null, { status: 204 }));
    stub.routes["POST /bff/api/v1/documents"] = () =>
      problem(422, "validation_error", {
        errors: [
          {
            field: "acl",
            code: "acl_required_for_scoped_upload",
            message_key: "errors.acl_required_for_scoped_upload",
          },
        ],
      });
    renderWithIntl(<NewDocumentScreen />);
    await userEvent.upload(
      await screen.findByLabelText("File to upload"),
      new File(["%PDF"], "homework.pdf", { type: "application/pdf" }),
    );
    await userEvent.type(screen.getByRole("textbox", { name: /Title/ }), "Homework plan");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Type" }), "other");
    await userEvent.click(screen.getByRole("radio", { name: "Only some staff" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "Principal" }));
    await userEvent.click(screen.getByRole("button", { name: "Upload a document" }));
    expect(
      await screen.findByText(/Choose your own classes or sections under/),
    ).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});

describe("documents pages and menu (NFR-I18N-001)", () => {
  const params = (value: string = DOC) => Promise.resolve({ locale: "en", documentId: value });
  const pages: Array<{
    name: string;
    title: (m: (typeof messages)["en"]) => string;
    render: () => Promise<ReactElement> | ReactElement;
  }> = [
    {
      name: "list",
      title: (m) => m.documents.title,
      render: () => DocumentsPage({ searchParams: Promise.resolve({}) }),
    },
    { name: "upload", title: (m) => m.documents.new.title, render: () => <NewDocumentPage /> },
    {
      name: "one document",
      title: (m) => m.documents.detail.title,
      render: () => DocumentPage({ params: params() }),
    },
  ];

  for (const locale of ["en", "te"] as Locale[]) {
    for (const item of pages) {
      it(`${item.name} [${locale}]`, async () => {
        setMe([READ, UPLOAD]);
        renderWithIntl(await item.render(), locale);
        expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
          item.title(messages[locale]),
        );
      });
    }
  }

  it("refuses ids that are not UUIDs before calling the API", async () => {
    await expect(DocumentPage({ params: params("../me") })).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("the school menu offers documents only with document.read", () => {
    renderWithIntl(<SchoolShell permissions={[READ]}>content</SchoolShell>);
    const nav = screen.getByRole("navigation", { name: messages.en.school.nav.label });
    expect(within(nav).getByRole("link", { name: "Documents" })).toHaveAttribute(
      "href",
      "/en/documents",
    );
  });

  it("hides documents from the menu without document.read", () => {
    renderWithIntl(<SchoolShell permissions={[READ_BASIC]}>content</SchoolShell>);
    const nav = screen.getByRole("navigation", { name: messages.en.school.nav.label });
    expect(within(nav).queryByRole("link", { name: "Documents" })).toBeNull();
  });
});
