"use client";

import type { ApiClient, components } from "@schoolos/api-client";
import { newIdempotencyKey, unwrap } from "@/lib/bff/query";

/**
 * Upload one file the documents way (docs/09 §4 Documents; FR-DOC-001..005):
 *
 * 1. POST /documents/uploads → a presigned POST (exact key, Content-Type and size, ≤ 10 min).
 * 2. The browser posts the file straight to storage (never through the BFF; no token needed).
 * 3. POST /documents registers it (202: version 1 queued for the malware scan).
 * 4. GET /documents/{id} until the scan passed (`ready`); `quarantined`/`failed` stop.
 *
 * Nothing about the file's contents is logged or sent anywhere else.
 */

export type UploadPurpose = components["schemas"]["UploadCreate"]["purpose"];

export type UploadStage = "requesting" | "uploading" | "registering" | "scanning" | "ready";

export interface UploadProgress {
  stage: UploadStage;
  /** 0–100 while uploading, when the browser reports it. */
  percent?: number;
}

export type UploadErrorCode =
  "network" | "storage_rejected" | "scan_failed" | "quarantined" | "scan_timeout";

/** A failure outside the API's problem+json answers (storage, scan outcome, time-out). */
export class UploadError extends Error {
  constructor(readonly code: UploadErrorCode) {
    super(`upload failed: ${code}`);
    this.name = "UploadError";
  }
}

/** Declared types by extension: browsers report CSV inconsistently (Windows says ms-excel). */
const TYPES: Record<string, string> = {
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  csv: "text/csv",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  pdf: "application/pdf",
};

export function contentTypeOf(file: Pick<File, "name" | "type">): string {
  const ext = file.name.includes(".") ? (file.name.split(".").pop() ?? "").toLowerCase() : "";
  return TYPES[ext] ?? (file.type || "application/octet-stream");
}

export function extensionOf(name: string): string {
  return name.includes(".") ? (name.split(".").pop() ?? "").toLowerCase() : "";
}

/** Document title from the file name (API: 1–200 characters, no control characters). */
export function titleOf(name: string): string {
  // Strip control characters the API refuses.
  const clean = name.replace(/[\u0000-\u001f\u007f]/g, " ").trim();
  return (clean || "file").slice(0, 200);
}

export type StoragePost = (
  url: string,
  fields: Readonly<Record<string, string>>,
  file: File,
  onProgress: (fraction: number) => void,
) => Promise<void>;

/**
 * Presigned POST with XMLHttpRequest, which (unlike fetch) reports upload progress: office
 * connections are slow and a 10 MB file should never look stuck.
 */
export const xhrStoragePost: StoragePost = (url, fields, file, onProgress) =>
  new Promise((resolve, reject) => {
    const form = new FormData();
    for (const [name, value] of Object.entries(fields)) form.append(name, value);
    form.append("file", file); // must be the last field of a presigned POST
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new UploadError("storage_rejected"));
    xhr.onerror = () => reject(new UploadError("network"));
    xhr.onabort = () => reject(new UploadError("network"));
    xhr.send(form);
  });

let storagePostOverride: StoragePost | undefined;
/** Tests only: replace the storage POST (jsdom has no network). */
export function setStoragePostForTesting(post: StoragePost | undefined): void {
  storagePostOverride = post;
}

export interface UploadOptions {
  onProgress?: (progress: UploadProgress) => void;
  /**
   * Who may see the uploaded file (e.g. one section, for a class teacher's attendance sheet,
   * FR-ATT-004). Scoped uploaders must name their own section or class; empty = the default.
   */
  acl?: components["schemas"]["DocumentCreate"]["acl"];
  /** Poll interval while the malware scan runs. */
  pollMs?: number;
  /** Give up waiting for the scan after this long (the document stays; try again later). */
  timeoutMs?: number;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Wait for GET /documents/{id} to report the current version `ready`. */
export async function waitUntilScanned(
  api: ApiClient,
  documentId: string,
  { pollMs = 2000, timeoutMs = 180_000 }: Pick<UploadOptions, "pollMs" | "timeoutMs"> = {},
): Promise<void> {
  const started = Date.now();
  for (;;) {
    const doc = await unwrap(
      api.GET("/api/v1/documents/{document_id}", {
        params: { path: { document_id: documentId } },
      }),
    );
    const status = doc.current_version?.status;
    if (status === "ready") return;
    if (status === "quarantined") throw new UploadError("quarantined");
    if (status === "failed") throw new UploadError("scan_failed");
    if (Date.now() - started > timeoutMs) throw new UploadError("scan_timeout");
    await sleep(pollMs);
  }
}

/** Upload, register and wait for the scan; returns the new document's id. */
export async function uploadDocument(
  api: ApiClient,
  file: File,
  purpose: UploadPurpose,
  options: UploadOptions = {},
): Promise<string> {
  const report = options.onProgress ?? (() => undefined);
  report({ stage: "requesting" });
  const upload = await unwrap(
    api.POST("/api/v1/documents/uploads", {
      headers: { "Idempotency-Key": newIdempotencyKey() },
      body: {
        filename: file.name.slice(0, 255) || "file",
        content_type: contentTypeOf(file),
        size_bytes: file.size,
        purpose,
      },
    }),
  );
  report({ stage: "uploading", percent: 0 });
  await (storagePostOverride ?? xhrStoragePost)(upload.url, upload.fields, file, (fraction) =>
    report({ stage: "uploading", percent: Math.round(fraction * 100) }),
  );
  report({ stage: "registering" });
  const doc = await unwrap(
    api.POST("/api/v1/documents", {
      headers: { "Idempotency-Key": newIdempotencyKey() },
      body: {
        upload_id: upload.upload_id,
        title: titleOf(file.name),
        ...(options.acl?.length ? { acl: options.acl } : {}),
      },
    }),
  );
  report({ stage: "scanning" });
  await waitUntilScanned(api, doc.id, options);
  report({ stage: "ready" });
  return doc.id;
}
