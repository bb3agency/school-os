import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setStoragePostForTesting } from "@/features/imports/upload";
import { permissionsFrom } from "@/features/students/me";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import {
  ALL_RECORD_PERMISSIONS,
  ATTRIBUTES,
  ID,
  NOW,
  extractionBatch,
  extractionItem,
  extractionPage,
  fakeAadhaar,
  summary,
} from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { afterActionOf } from "./after";
import { BatchView, lowestConfidence } from "./BatchScreen";
import {
  ItemReviewView,
  NextItemScreen,
  confirmBody,
  fieldBox,
  genderOf,
  imageRefreshMs,
} from "./ItemReview";
import { UploadPhotos, checkPhotos } from "./RegisterPhotosScreen";

const replace = vi.fn();
vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/register-photos",
    useRouter: () => ({ push: vi.fn(), replace, refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/**
 * Register-photo verification queue (US-402, FR-IMP-020..023, PRV-016, invariant 4).
 * Synthetic data only.
 */

const xm = messages.en.extraction;
const ready = <T,>(data: T) => ({ status: "ready" as const, data });
const perms = permissionsFrom(ALL_RECORD_PERMISSIONS);
let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  replace.mockReset();
});
afterEach(() => {
  setStoragePostForTesting(undefined);
  uninstallBffStub();
});

function photo(name: string, size = 2048) {
  const blob = new File(["x"], name, { type: name.endsWith(".png") ? "image/png" : "image/jpeg" });
  Object.defineProperty(blob, "size", { value: size });
  return blob;
}

function view(item = extractionItem(), permissions = perms, batch = extractionBatch()) {
  const done = vi.fn();
  const broken = vi.fn();
  const result = renderWithIntl(
    <ItemReviewView
      item={ready(item)}
      batch={ready(batch)}
      attributes={ready(ATTRIBUTES)}
      permissions={permissions}
      sections={[{ value: ID.section, label: "Class 9 · A" }]}
      onDone={done}
      onImageBroken={broken}
    />,
  );
  return { ...result, done, broken };
}

describe("helpers", () => {
  it("checks photos before anything is sent (JPG/PNG, at most 50, no PDF)", () => {
    expect(checkPhotos([])).toBe("photosMissing");
    expect(checkPhotos([photo("p1.jpg"), photo("p2.PNG")])).toBeNull();
    expect(checkPhotos([photo("p1.pdf")])).toBe("photoPdf");
    expect(checkPhotos([photo("p1.heic")])).toBe("photoType");
    expect(checkPhotos([photo("p1.jpg", 26 * 1024 * 1024)])).toBe("photoTooLarge");
    expect(checkPhotos(Array.from({ length: 51 }, (_, i) => photo(`p${i}.jpg`)))).toBe(
      "photosTooMany",
    );
  });

  it("reads bounding boxes, genders and the refresh time of the image link", () => {
    expect(
      fieldBox({
        value: "x",
        confidence: 1,
        bbox: [0.1, 0.2, 0.3, 0.05],
        masked: false,
        low_confidence: false,
      }),
    ).toEqual([0.1, 0.2, 0.3, 0.05]);
    expect(
      fieldBox({
        value: "x",
        confidence: 1,
        bbox: [0.1, 0.2, 3, 0.05],
        masked: false,
        low_confidence: false,
      }),
    ).toBeNull();
    expect(fieldBox(undefined)).toBeNull();
    expect(genderOf("F")).toBe("female");
    expect(genderOf("Boy")).toBe("male");
    expect(genderOf("?")).toBe("");
    const now = Date.parse(NOW);
    expect(imageRefreshMs(extractionItem(), now)).toBe(270_000);
    expect(imageRefreshMs(extractionItem({ image: null }), now)).toBe(false);
    expect(afterActionOf("confirmed")).toBe("confirmed");
    expect(afterActionOf("<script>")).toBeUndefined();
  });

  it("never sends masked values back and links an existing student without a section", () => {
    const editable = ["admission_no", "full_name", "dob"];
    expect(
      confirmBody(
        {
          admission_no: "1987/0012",
          full_name: "Lakshmi",
          dob: "",
          student_id: "",
          section_id: ID.section,
          roll_no: "4",
          student_status: "active",
        },
        editable,
      ),
    ).toEqual({
      fields: { admission_no: "1987/0012", full_name: "Lakshmi", dob: null },
      student_status: "active",
      section_id: ID.section,
      roll_no: "4",
    });
    expect(
      confirmBody(
        {
          admission_no: "1987/0012",
          full_name: "",
          dob: "2011-07-09",
          student_id: ID.student,
          section_id: ID.section,
          roll_no: "4",
          student_status: "active",
        },
        editable,
      ),
    ).toEqual({
      fields: { admission_no: "1987/0012", full_name: null, dob: "2011-07-09" },
      student_status: "active",
      student_id: ID.student,
    });
  });
});

describe("US-402 AC1: upload register photos", () => {
  it("uploads each photo as a register scan, then starts one batch", async () => {
    let n = 0;
    stub.routes["POST /bff/api/v1/documents/uploads"] = () =>
      Response.json({ upload_id: ID.upload, url: "https://files.schoolos.example/u", fields: {} });
    stub.routes["POST /bff/api/v1/documents"] = () => {
      n += 1;
      return Response.json({ id: n === 1 ? ID.doc : ID.page2 }, { status: 202 });
    };
    stub.routes[`GET /bff/api/v1/documents/${ID.doc}`] = () =>
      Response.json({ id: ID.doc, current_version: { status: "ready" } });
    stub.routes[`GET /bff/api/v1/documents/${ID.page2}`] = () =>
      Response.json({ id: ID.page2, current_version: { status: "ready" } });
    stub.routes["POST /bff/api/v1/extraction-batches"] = () =>
      Response.json(extractionBatch({ status: "queued" }), { status: 202 });
    setStoragePostForTesting(async () => undefined);
    const started = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(<UploadPhotos onStarted={started} />);

    await user.upload(screen.getByLabelText(xm.upload.photos), [photo("p1.pdf")]);
    await user.click(screen.getByRole("button", { name: xm.upload.submit }));
    expect(screen.getByText(xm.upload.photoPdf)).toBeInTheDocument();

    await user.upload(screen.getByLabelText(xm.upload.photos), [photo("p1.jpg"), photo("p2.png")]);
    await user.click(screen.getByRole("button", { name: xm.upload.submit }));
    await waitFor(() => expect(started).toHaveBeenCalled());
    const uploads = stub
      .callsTo("POST /bff/api/v1/documents/uploads")
      .map((c) => JSON.parse(c.body));
    expect(uploads.map((u: { purpose: string }) => u.purpose)).toEqual([
      "register_scan",
      "register_scan",
    ]);
    expect(
      JSON.parse(stub.callsTo("POST /bff/api/v1/extraction-batches")[0]?.body ?? "{}"),
    ).toEqual({
      document_ids: [ID.doc, ID.page2],
    });
  });
});

describe("US-402 AC4 / PRV-016: a batch", () => {
  it("shows progress per page, which photos were blacked out or withheld, and the queue", async () => {
    stub.routes["GET /bff/api/v1/extraction-items"] = () =>
      page([{ ...extractionItem(), page_id: ID.page2 }]);
    const batch = extractionBatch({
      pages_withheld: 1,
      pages: [
        extractionPage({ image_redacted: true, aadhaar_detected: true }),
        extractionPage({ id: ID.page2, seq: 2, image_withheld: true, aadhaar_detected: true }),
      ],
    });
    renderWithIntl(<BatchView batch={ready(batch)} permissions={perms} />);
    expect(screen.getByText(xm.pages.redacted)).toBeInTheDocument();
    expect(screen.getByText(xm.pages.withheld)).toBeInTheDocument();
    expect(screen.getByText("1 photo was withheld")).toBeInTheDocument();
    const link = await screen.findByRole("link", { name: /Check this row\s*Page 2, row 1/ });
    expect(link).toHaveAttribute("href", `/en/register-photos/items/${ID.item}`);
    expect(screen.getByText("1 unclear value")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: xm.batch.start })).toHaveAttribute(
      "href",
      `/en/register-photos/${ID.batch}/next`,
    );
    const query = stub.callsTo("GET /bff/api/v1/extraction-items")[0]?.url.searchParams;
    expect(query?.get("batch_id")).toBe(ID.batch);
    expect(query?.get("status")).toBe("pending_review");
  });

  it("NFR-A11Y-001: queue cards show the lowest certainty only when the reader gave one", async () => {
    const unsure = extractionItem();
    const plain = {
      ...extractionItem({ id: ID.page2, row_index: 1, low_confidence: false }),
      low_confidence_fields: [],
      fields: { full_name: { ...unsure.fields.full_name!, confidence: null } },
    };
    stub.routes["GET /bff/api/v1/extraction-items"] = () => page([unsure, plain]);
    renderWithIntl(<BatchView batch={ready(extractionBatch())} permissions={perms} />);
    const queue = await screen.findByRole("list", { name: xm.queue.title });
    expect(within(queue).getAllByRole("listitem")).toHaveLength(2);
    const rings = within(queue).getAllByRole("progressbar");
    expect(rings).toHaveLength(1);
    expect(rings[0]).toHaveAttribute("aria-valuenow", "41");
    expect(lowestConfidence(plain)).toBeNull();
    // Masked values stay masked on the cards too (the API sends only the masked text).
    expect(within(queue).queryByText(/\d{12}/)).toBeNull();
  });

  it("announces that pages are still being read", () => {
    stub.routes["GET /bff/api/v1/extraction-items"] = () => page([]);
    renderWithIntl(
      <BatchView
        batch={ready(extractionBatch({ status: "processing", pages_done: 1 }))}
        permissions={perms}
      />,
    );
    expect(screen.getByText(xm.batch.busyTitle)).toBeInTheDocument();
    expect(screen.getByText("1 of 2 pages read. This page updates by itself.")).toBeInTheDocument();
  });
});

describe("US-402 AC1/AC2: check one row beside its page photo", () => {
  it("shows the photo beside the row, flags hard-to-read values and keeps masked values masked", () => {
    view();
    expect(screen.getByRole("img", { name: "Photo of register page 1" })).toHaveAttribute(
      "src",
      expect.stringContaining("https://files.schoolos.example/"),
    );
    expect(screen.getByText("1 value was hard to read")).toBeInTheDocument();
    const dob = screen.getByLabelText(/Date of birth/);
    expect(dob).toHaveValue("09/07/2011");
    expect(dob.closest("div")?.parentElement?.textContent).toContain(xm.review.checkThis);
    expect(screen.getByText(/Read with 41% certainty/)).toBeInTheDocument();
    // Masked value: shown masked, never editable, never a full number anywhere on the page.
    expect(screen.getByText("XXXX XXXX 4821")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Father's name/)).toBeNull();
    expect(document.body.textContent).not.toMatch(/\d{12}/);
    expect(screen.getByLabelText(/Gender/)).toHaveValue("female");
  });

  it("saves the row as read (dates as ISO), without the masked field, with an Idempotency-Key", async () => {
    stub.routes[`POST /bff/api/v1/extraction-items/${ID.item}/confirm`] = () =>
      Response.json({ ...extractionItem(), status: "confirmed" });
    const user = userEvent.setup();
    const { done } = view();
    const dob = screen.getByLabelText(/Date of birth/);
    await user.clear(dob);
    await user.type(dob, "10/07/2011");
    await user.selectOptions(screen.getByLabelText(xm.review.section), ID.section);
    await user.click(screen.getByRole("button", { name: xm.review.confirm }));
    await waitFor(() => expect(done).toHaveBeenCalledWith("confirmed"));
    const call = stub.callsTo(`POST /bff/api/v1/extraction-items/${ID.item}/confirm`)[0];
    expect(call?.headers.get("idempotency-key")).toBeTruthy();
    const body = JSON.parse(call?.body ?? "{}");
    expect(body.fields).toMatchObject({
      admission_no: "1987/0012",
      full_name: "Lakshmi Prasanna B.",
      dob: "2011-07-10",
      gender: "female",
    });
    expect(body.fields).not.toHaveProperty("father_name");
    expect(body).toMatchObject({ section_id: ID.section, student_status: "active" });
    expect(body).not.toHaveProperty("student_id");
  });

  it("refuses a typed full Aadhaar number before sending", async () => {
    const user = userEvent.setup();
    view();
    const mother = screen.getByLabelText(/Mother's name/);
    await user.type(mother, fakeAadhaar());
    await user.click(screen.getByRole("button", { name: xm.review.confirm }));
    expect(
      (await screen.findAllByText(messages.en.students.aadhaarNotAllowed)).length,
    ).toBeGreaterThan(0);
    expect(stub.callsTo(`POST /bff/api/v1/extraction-items/${ID.item}/confirm`)).toHaveLength(0);
  });

  it("links the row to a student with the same admission number instead of creating a second one", async () => {
    stub.routes[`POST /bff/api/v1/extraction-items/${ID.item}/confirm`] = () =>
      problem(403, "identity_change_required");
    const user = userEvent.setup();
    view(
      extractionItem({
        possible_matches: [summary({ id: ID.student2, display_name: "Lakshmi P." })],
      }),
    );
    await user.click(screen.getByRole("radio", { name: /Lakshmi P\./ }));
    expect(screen.queryByLabelText(xm.review.section)).toBeNull();
    await user.click(screen.getByRole("button", { name: xm.review.confirm }));
    expect(
      await screen.findByText(messages.en.students.errors.identity_change_required.title),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: xm.review.goToChangeRequests })).toHaveAttribute(
      "href",
      `/en/change-requests?student_id=${ID.student2}`,
    );
    const body = JSON.parse(
      stub.callsTo(`POST /bff/api/v1/extraction-items/${ID.item}/confirm`)[0]?.body ?? "{}",
    );
    expect(body.student_id).toBe(ID.student2);
    expect(body).not.toHaveProperty("section_id");
  });

  it("PRV-016: a withheld page shows no photo and can't be saved, but can be discarded", async () => {
    stub.routes[`POST /bff/api/v1/extraction-items/${ID.item}/reject`] = () =>
      Response.json({ ...extractionItem(), status: "rejected", reject_reason: "unreadable" });
    const user = userEvent.setup();
    const { done } = view(
      extractionItem({ image: null, image_unavailable: "withheld_sensitive_number" }),
    );
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText(xm.image.withheld_sensitive_number.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: xm.review.confirm })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: xm.reject.open }));
    const dialog = screen.getByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText(xm.reject.reason), "unreadable");
    await user.click(within(dialog).getByRole("button", { name: xm.reject.submit }));
    await waitFor(() => expect(done).toHaveBeenCalledWith("rejected"));
    expect(
      JSON.parse(
        stub.callsTo(`POST /bff/api/v1/extraction-items/${ID.item}/reject`)[0]?.body ?? "{}",
      ),
    ).toEqual({ reason: "unreadable" });
  });

  it("says when a blacked-out photo is shown, or the photo can't be opened", () => {
    const { unmount } = view(
      extractionItem(),
      perms,
      extractionBatch({
        pages: [extractionPage({ image_redacted: true, aadhaar_detected: true })],
      }),
    );
    expect(screen.getByText(xm.image.redacted)).toBeInTheDocument();
    unmount();
    view(extractionItem({ image: null, image_unavailable: "not_visible" }));
    expect(screen.getByText(xm.image.not_visible.title)).toBeInTheDocument();
  });

  it("without import.commit the row can be read but not saved or discarded", () => {
    view(extractionItem(), permissionsFrom(["import.run", "student.read_basic"]));
    expect(screen.getByText(xm.review.noPermissionTitle)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: xm.review.confirm })).toBeNull();
    expect(screen.queryByRole("button", { name: xm.reject.open })).toBeNull();
  });

  it("explains a row someone else already checked (409)", async () => {
    stub.routes[`POST /bff/api/v1/extraction-items/${ID.item}/confirm`] = () =>
      problem(409, "item_already_reviewed");
    const user = userEvent.setup();
    view();
    await user.click(screen.getByRole("button", { name: xm.review.confirm }));
    expect(await screen.findByText(xm.errors.item_already_reviewed.title)).toBeInTheDocument();
  });

  it("opens the next row waiting for review, or goes back to the batch", async () => {
    stub.routes["GET /bff/api/v1/extraction-items"] = () =>
      page([{ ...extractionItem(), id: ID.item2 }]);
    const { unmount } = renderWithIntl(<NextItemScreen batchId={ID.batch} after="confirmed" />);
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith(`/en/register-photos/items/${ID.item2}?after=confirmed`),
    );
    unmount();
    replace.mockReset();
    stub.routes["GET /bff/api/v1/extraction-items"] = () => page([]);
    renderWithIntl(<NextItemScreen batchId={ID.batch} />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith(`/en/register-photos/${ID.batch}`));
  });
});
