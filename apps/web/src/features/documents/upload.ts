import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import { GENERAL_TYPES, MAX_UPLOAD_BYTES, SCAN_EXTENSIONS, type Purpose } from "./types";

/**
 * Document upload through the presigned flow (docs/09 Documents, FR-DOC-001..003):
 *
 * 1. `POST /documents/uploads` → a presigned POST for exactly this file (≤ 10 minutes);
 * 2. the browser posts the file straight to storage, never through the BFF (no file bodies in
 *    the web tier; CSP `connect-src` allows the configured FILES_ORIGIN);
 * 3. `POST /documents` (new document) or `POST /documents/{id}/versions` (new version)
 *    registers it: 202, the version is queued for the virus scan.
 *
 * The API checks the file again by its content, not its name.
 */

type DocumentCreate = components["schemas"]["DocumentCreate"];
type DocumentOut = components["schemas"]["DocumentOut"];

export type FileProblem = "fileRequired" | "fileEmpty" | "fileType" | "fileTooLarge";

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

/**
 * The content type to declare, from the file name (office PCs often report an empty type for
 * DOCX/XLSX). Null when the kind is not accepted for the purpose.
 */
export function contentTypeFor(file: Pick<File, "name">, purpose: Purpose): string | null {
  const ext = extensionOf(file.name);
  const scansOnly = purpose === "evidence" || purpose === "register_scan";
  if (scansOnly && !(SCAN_EXTENSIONS as readonly string[]).includes(ext)) return null;
  return GENERAL_TYPES[ext] ?? null;
}

/** Client-side check before anything is sent; null when the file is acceptable. */
export function checkFile(file: File | null | undefined, purpose: Purpose): FileProblem | null {
  if (!file) return "fileRequired";
  if (file.size === 0) return "fileEmpty";
  if (contentTypeFor(file, purpose) === null) return "fileType";
  if (file.size > MAX_UPLOAD_BYTES) return "fileTooLarge";
  return null;
}

/** Storage refused the file (expired form, size or type mismatch, network). */
export class StorageUploadError extends Error {
  constructor(readonly status: number) {
    super(`storage upload failed (${status})`);
    this.name = "StorageUploadError";
  }
}

type StorageSend = (url: string, init: RequestInit) => Promise<Response>;
let storageSendOverride: StorageSend | undefined;
/** Tests only: jsdom's FormData cannot be sent through the test fetch double. */
export function setDocumentStorageSendForTesting(send: StorageSend | undefined): void {
  storageSendOverride = send;
}

export interface UploadKeys {
  /** Idempotency-Key for POST /documents/uploads. */
  upload: string;
  /** Idempotency-Key for POST /documents or /documents/{id}/versions. */
  register: string;
}

/** Steps 1 and 2: returns the upload id to register. */
async function sendFile(
  api: ApiClient,
  file: File,
  purpose: Purpose,
  key: string,
  documentId?: string,
): Promise<string> {
  const presigned = await unwrap(
    api.POST("/api/v1/documents/uploads", {
      headers: { "Idempotency-Key": key },
      body: {
        filename: file.name,
        content_type: contentTypeFor(file, purpose) ?? file.type,
        size_bytes: file.size,
        purpose,
        ...(documentId ? { document_id: documentId } : {}),
      },
    }),
  );
  const form = new FormData();
  for (const [name, value] of Object.entries(presigned.fields)) form.append(name, value);
  // The file must be the last field of an S3 POST policy form.
  form.append("file", file);
  let stored: Response;
  try {
    stored = await (storageSendOverride ?? fetch)(presigned.url, {
      method: "POST",
      body: form,
      credentials: "omit",
    });
  } catch {
    throw new StorageUploadError(0);
  }
  if (!stored.ok) throw new StorageUploadError(stored.status);
  return presigned.upload_id;
}

export type NewDocument = Omit<DocumentCreate, "upload_id">;

/** Upload a new document; returns it (version 1 queued for the virus scan). */
export async function uploadDocument(
  api: ApiClient,
  file: File,
  purpose: Purpose,
  metadata: NewDocument,
  keys: UploadKeys,
): Promise<DocumentOut> {
  const uploadId = await sendFile(api, file, purpose, keys.upload);
  return unwrap(
    api.POST("/api/v1/documents", {
      headers: { "Idempotency-Key": keys.register },
      body: { ...metadata, upload_id: uploadId },
    }),
  );
}

/** Upload the next version of a document (history is kept, FR-DOC-006). */
export async function uploadVersion(
  api: ApiClient,
  documentId: string,
  file: File,
  purpose: Purpose,
  keys: UploadKeys,
): Promise<DocumentOut> {
  const uploadId = await sendFile(api, file, purpose, keys.upload, documentId);
  return unwrap(
    api.POST("/api/v1/documents/{document_id}/versions", {
      params: { path: { document_id: documentId } },
      headers: { "Idempotency-Key": keys.register },
      body: { upload_id: uploadId },
    }),
  );
}

/**
 * Two Idempotency-Keys per form intent. `useApiForm` hands one key per intent (kept when a
 * request fails on the network, new after success or a 4xx); after storage refused the file
 * the next try must ask for a fresh presigned form, so both keys are renewed.
 */
export function createUploadKeys(newKey: () => string) {
  let intent = "";
  let keys: UploadKeys = { upload: "", register: "" };
  return {
    for(intentKey: string): UploadKeys {
      if (intentKey !== intent) {
        intent = intentKey;
        keys = { upload: intentKey, register: newKey() };
      }
      return keys;
    },
    storageFailed(): void {
      keys = { upload: newKey(), register: newKey() };
    },
  };
}
