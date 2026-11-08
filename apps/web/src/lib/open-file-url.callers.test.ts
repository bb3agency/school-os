import { afterEach, describe, expect, it } from "vitest";
import * as admin from "@/features/admin/data";
import * as circulars from "@/features/circulars/data";
import * as disclosure from "@/features/disclosure/data";
import * as documents from "@/features/documents/data";
import * as exportsData from "@/features/exports/data";
import { UnsafeFileUrlError } from "./open-file-url";

type Opener = ((url: string) => void) | null;

/** Every feature's download starter (WS-03): the opener sees only an https link. */
const STARTERS: Array<[string, (opener: Opener) => void, (url: string) => void]> = [
  ["admin", admin.setAdminDownloadOpenerForTesting, admin.startDownload],
  ["circulars", circulars.setNoticeDownloadOpenerForTesting, circulars.startDownload],
  ["disclosure", disclosure.setDisclosureDownloadOpenerForTesting, disclosure.startDownload],
  ["documents", documents.setDocumentDownloadOpenerForTesting, documents.startDocumentDownload],
  ["exports", exportsData.setDownloadOpenerForTesting, exportsData.startDownload],
];

afterEach(() => {
  for (const [, setOpener] of STARTERS) setOpener(null);
});

describe.each(STARTERS)("%s download starter (SEC-010 WS-03)", (_name, setOpener, start) => {
  it("opens an https link and refuses javascript:, data: and relative links", () => {
    const opened: string[] = [];
    setOpener((url) => opened.push(url));
    start("https://files.schoolos.example/t/x/file.pdf?sig=synthetic");
    for (const bad of ["javascript:alert(1)", "data:text/html,x", "/bff/api/v1/x"]) {
      expect(() => start(bad)).toThrow(UnsafeFileUrlError);
    }
    expect(opened).toEqual(["https://files.schoolos.example/t/x/file.pdf?sig=synthetic"]);
  });
});
