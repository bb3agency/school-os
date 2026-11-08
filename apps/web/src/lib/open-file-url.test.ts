import { afterEach, describe, expect, it, vi } from "vitest";
import {
  openFileUrl,
  safeFileUrl,
  setFileUrlNavigatorForTesting,
  UnsafeFileUrlError,
} from "./open-file-url";

const HTTPS_PAGE = new URL("https://office.school.example/documents");
const LOCAL_PAGE = new URL("http://localhost:3000/documents");
const PRESIGNED = "https://files.school.example/t/x/doc.pdf?X-Amz-Signature=abc";

afterEach(() => setFileUrlNavigatorForTesting(null));

describe("safeFileUrl (WS-03, SEC-010)", () => {
  it("accepts an absolute https link", () => {
    expect(safeFileUrl(PRESIGNED, HTTPS_PAGE)).toBe(PRESIGNED);
  });

  it.each([
    ["javascript:", "javascript:alert(document.cookie)"],
    ["javascript: with spaces and case", "  JaVaScRiPt:alert(1)"],
    ["data:", "data:text/html,<script>alert(1)</script>"],
    ["blob:", "blob:https://office.school.example/1234"],
    ["relative", "/bff/api/v1/documents/1"],
    ["protocol-relative", "//evil.example/x.pdf"],
    ["http on another host", "http://files.school.example/doc.pdf"],
    ["credentials in the link", "https://user:pass@files.school.example/doc.pdf"],
    ["empty", ""],
  ])("refuses %s", (_label, value) => {
    expect(safeFileUrl(value, HTTPS_PAGE)).toBeNull();
  });

  it("refuses http even on localhost when the page is https (every real deployment)", () => {
    expect(safeFileUrl("http://localhost:8099/e2e-files/a.pdf", HTTPS_PAGE)).toBeNull();
  });

  it("accepts http on localhost only from a page served over http on localhost (local runs, e2e stand-in)", () => {
    expect(safeFileUrl("http://localhost:8099/e2e-files/a.pdf", LOCAL_PAGE)).toBe(
      "http://localhost:8099/e2e-files/a.pdf",
    );
    expect(safeFileUrl("http://files.example/a.pdf", LOCAL_PAGE)).toBeNull();
  });

  it("refuses anything that is not a string", () => {
    expect(safeFileUrl(undefined, HTTPS_PAGE)).toBeNull();
    expect(safeFileUrl({ href: PRESIGNED }, HTTPS_PAGE)).toBeNull();
  });
});

describe("openFileUrl (WS-03)", () => {
  it("navigates to a safe link and throws for an unsafe one without navigating", () => {
    const navigate = vi.fn();
    setFileUrlNavigatorForTesting(navigate);
    openFileUrl(PRESIGNED);
    expect(navigate).toHaveBeenCalledWith(PRESIGNED);
    expect(() => openFileUrl("javascript:alert(1)")).toThrow(UnsafeFileUrlError);
    expect(navigate).toHaveBeenCalledTimes(1);
  });
});
