import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../../messages/en.json";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { CitedPageButton, citedPageUrl, setPageOpenerForTesting } from "./ResolveSource";

// D73: an Ask source chip opens the cited page of a document (GET /documents/{id}/pages/{n})
// in a new tab at #page=n; any error falls back to the document's own screen. Synthetic data.

const nav = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/ask",
    useRouter: () => ({ push: nav.push, replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const A = en.ask.answer;
const DOC = "0192f3a4-0000-7000-8000-00000000c701";
const PAGE_ROUTE = `GET /bff/api/v1/documents/${DOC}/pages/3`;
const PRESIGNED = "https://sos-test-files.s3.ap-south-1.amazonaws.com/t/x/docs/y/v2/original.pdf";

function link(overrides: Record<string, unknown> = {}) {
  return {
    url: `${PRESIGNED}?X-Amz-Expires=300&X-Amz-Signature=abc`,
    page: 3,
    expires_at: "2026-11-08T10:05:00Z",
    content_type: "application/pdf",
    version_no: 2,
    ...overrides,
  };
}

let stub: BffStub;
let opened: string[];

beforeEach(() => {
  nav.push.mockReset();
  stub = installBffStub("staff");
  opened = [];
  setPageOpenerForTesting((url) => opened.push(url));
});

afterEach(() => {
  setPageOpenerForTesting(undefined);
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function renderChip() {
  renderWithIntl(<CitedPageButton documentId={DOC} version={2} page={3} name="Fee circular" />);
  return screen.getByRole("button", { name: /^page 3 \(open Fee circular/ });
}

describe("cited page chip (D73)", () => {
  it("asks for the cited version's page and opens it in a new tab at #page=n", async () => {
    let version = "";
    stub.routes[PAGE_ROUTE] = (_request, url) => {
      version = url.searchParams.get("version") ?? "";
      return Response.json(link());
    };
    const chip = renderChip();
    expect(chip).toHaveTextContent("page 3");
    await userEvent.setup().click(chip);
    await waitFor(() => expect(opened).toHaveLength(1));
    expect(version).toBe("2");
    expect(opened[0]).toMatch(/^https:\/\/sos-test-files\.s3\.[^#]+X-Amz-Signature=abc#page=3$/);
    expect(nav.push).not.toHaveBeenCalled();
  });

  it.each([
    ["no longer visible", () => problem(404, "not_found")],
    ["not scanned yet", () => problem(409, "document_not_ready")],
    ["not shown in a browser", () => problem(415, "page_not_viewable")],
    ["a malformed answer", () => Response.json({ url: 42 })],
    ["a link that is not http(s)", () => Response.json(link({ url: "javascript:alert(1)" }))],
  ])("falls back to the document screen on %s", async (_label, answer) => {
    stub.routes[PAGE_ROUTE] = answer;
    await userEvent.setup().click(renderChip());
    await waitFor(() => expect(nav.push).toHaveBeenCalled());
    expect(String(nav.push.mock.calls[0]?.[0])).toContain(`/documents/${DOC}`);
    expect(opened).toEqual([]);
  });

  it("WF-02 (D73): opens the tab during the click, so a slow link is not blocked", async () => {
    setPageOpenerForTesting(undefined);
    const tab = { opener: {} as unknown, location: { href: "" }, close: vi.fn() };
    const open = vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    let answer: (response: Response) => void = () => undefined;
    stub.routes[PAGE_ROUTE] = () => new Promise<Response>((resolve) => (answer = resolve));
    try {
      await userEvent.setup().click(renderChip());
      // Browsers allow a new tab only during the click (user activation lasts a few seconds),
      // so the tab must exist before the API answers on a slow office link.
      expect(open).toHaveBeenCalledTimes(1);
      expect(tab.opener).toBeNull();
      answer(Response.json(link()));
      await waitFor(() => expect(tab.location.href).toMatch(/X-Amz-Signature=abc#page=3$/));
      expect(open).toHaveBeenCalledTimes(1);
      expect(nav.push).not.toHaveBeenCalled();
    } finally {
      open.mockRestore();
    }
  });

  it("WF-02 (D73): closes the waiting tab and opens the document screen when the link fails", async () => {
    setPageOpenerForTesting(undefined);
    const tab = { opener: {} as unknown, location: { href: "" }, close: vi.fn() };
    const open = vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    stub.routes[PAGE_ROUTE] = () => problem(404, "not_found");
    try {
      await userEvent.setup().click(renderChip());
      await waitFor(() => expect(nav.push).toHaveBeenCalled());
      expect(tab.close).toHaveBeenCalled();
      expect(tab.location.href).toBe("");
    } finally {
      open.mockRestore();
    }
  });

  it("adds #page only for PDFs and never keeps a hash from the API", () => {
    expect(citedPageUrl(link({ url: `${PRESIGNED}#evil` }), 3)).toBe(`${PRESIGNED}#page=3`);
    expect(citedPageUrl(link({ content_type: "image/png" }), 1)).not.toContain("#");
    expect(citedPageUrl(link({ url: "data:text/html,x" }), 1)).toBeNull();
    expect(citedPageUrl(link({ url: "not a url" }), 1)).toBeNull();
  });

  it("the hint follows the visible text, so the name starts with it in every language", () => {
    expect(A.openPageHint).not.toContain("{page}");
    expect(A.openPageHint).toContain("{title}");
  });
});
