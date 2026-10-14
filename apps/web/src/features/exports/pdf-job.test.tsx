import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { EXPORT_ID, exportRow, SECTION } from "@/test/school-fixtures";
import { setDownloadOpenerForTesting, setExportPollDelayForTesting } from "./data";
import { PdfJobButton } from "./PdfJobButton";

const STUDENT = "0192f3a4-0000-7000-8000-0000000000aa";
const SLIPS = "POST /bff/api/v1/exports/readiness-slips";
const FORMS = "POST /bff/api/v1/exports/apaar-consent-forms";
const ONE = `GET /bff/api/v1/exports/${EXPORT_ID}`;
const LINK = `GET /bff/api/v1/exports/${EXPORT_ID}/download-url`;

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  setExportPollDelayForTesting(() => 5);
});

afterEach(() => {
  setExportPollDelayForTesting(null);
  setDownloadOpenerForTesting(null);
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function slipsRow(status: "queued" | "ready" | "failed") {
  return exportRow({
    kind: "readiness_slips",
    profile_key: "bseap-ssc-2027",
    formats: ["pdf"],
    scope: { section_ids: [SECTION] },
    status,
    files:
      status === "ready" ? [{ format: "pdf", content_type: "application/pdf", size_bytes: 9 }] : [],
  });
}

describe("PDF of slips and forms (FR-DQ-047, FR-APC-007)", () => {
  it("asks the server for the slips PDF, waits for it and downloads it", async () => {
    const opened = vi.fn();
    setDownloadOpenerForTesting(opened);
    stub.routes[SLIPS] = () => Response.json(slipsRow("queued"), { status: 202 });
    let polls = 0;
    stub.routes[ONE] = () => Response.json(slipsRow(++polls > 1 ? "ready" : "queued"));
    stub.routes[LINK] = () =>
      Response.json({
        url: "https://files.invalid/presigned",
        expires_at: "2026-10-14T10:05:00Z",
        format: "pdf",
        content_type: "application/pdf",
        filename: "schoolos-verification-slips.pdf",
      });
    renderWithIntl(
      <PdfJobButton
        label="Make PDF of this section's slips"
        request={{
          kind: "readiness_slips",
          body: {
            profile_key: "bseap-ssc-2027",
            section_id: SECTION,
            include_ready: true,
            include_sensitive: false,
          },
        }}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Make PDF of this section's slips" }));
    const [call] = stub.callsTo(SLIPS);
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      profile_key: "bseap-ssc-2027",
      section_id: SECTION,
      include_ready: true,
      include_sensitive: false,
    });
    expect(call?.headers.get("Idempotency-Key")).toBeTruthy();
    const download = await screen.findByRole("button", { name: "Download PDF" });
    await userEvent.click(download);
    await waitFor(() => expect(opened).toHaveBeenCalledWith("https://files.invalid/presigned"));
    expect(stub.callsTo(LINK)[0]?.url.searchParams.get("format")).toBe("pdf");
  });

  it("shows why the consent forms could not be requested and offers another try after a failure", async () => {
    stub.routes[FORMS] = () => problem(409, "no_forms");
    renderWithIntl(
      <PdfJobButton
        label="Make PDF of the form"
        request={{ kind: "apaar_consent_forms", body: { student_id: STUDENT } }}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Make PDF of the form" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(JSON.parse(stub.callsTo(FORMS)[0]?.body ?? "{}")).toEqual({ student_id: STUDENT });

    stub.routes[FORMS] = () => Response.json(slipsRow("queued"), { status: 202 });
    stub.routes[ONE] = () => Response.json(slipsRow("failed"));
    await userEvent.click(screen.getByRole("button", { name: "Make PDF of the form" }));
    expect(await screen.findByText("The PDF could not be made.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Make it again" }));
    expect(screen.getByRole("button", { name: "Make PDF of the form" })).toBeInTheDocument();
  });
});
