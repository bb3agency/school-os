import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * Evidence upload through the documents presigned flow (docs/09 Documents):
 *
 * 1. `POST /documents/uploads` (purpose `evidence`) → a presigned POST for exactly this file;
 * 2. the browser posts the file straight to storage (never through the BFF: no file bodies
 *    in the web tier; CSP `connect-src` allows the configured FILES_ORIGIN);
 * 3. `POST /documents` registers it (202, version 1 queued for the virus scan) → document id.
 *
 * Evidence accepts PDF, JPG and PNG up to 25 MB (the API checks the content again).
 */
export const EVIDENCE_TYPES = ["application/pdf", "image/jpeg", "image/png"] as const;
export const EVIDENCE_ACCEPT = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";
export const EVIDENCE_MAX_BYTES = 25 * 1024 * 1024;

export type EvidenceProblem = "fileRequired" | "fileType" | "fileTooLarge" | "fileEmpty";

/** Client-side check before anything is sent; null when the file is acceptable. */
export function checkEvidence(file: File | null | undefined): EvidenceProblem | null {
  if (!file) return "fileRequired";
  if (file.size === 0) return "fileEmpty";
  if (!(EVIDENCE_TYPES as readonly string[]).includes(file.type)) return "fileType";
  if (file.size > EVIDENCE_MAX_BYTES) return "fileTooLarge";
  return null;
}

/** Storage refused the file (expired form, size or type mismatch, network). */
export class StorageUploadError extends Error {
  constructor(readonly status: number) {
    super(`storage upload failed (${status})`);
    this.name = "StorageUploadError";
  }
}

export interface UploadKeys {
  /** Idempotency-Key for POST /documents/uploads. */
  upload: string;
  /** Idempotency-Key for POST /documents. */
  register: string;
}

export type UploadStep = "presign" | "send" | "register";

type StorageSend = (url: string, init: RequestInit) => Promise<Response>;
let storageSendOverride: StorageSend | undefined;
/** Tests only: jsdom's FormData cannot be sent through the test fetch double. */
export function setStorageSendForTesting(send: StorageSend | undefined): void {
  storageSendOverride = send;
}

export async function uploadEvidence(
  api: ApiClient,
  file: File,
  title: string,
  keys: UploadKeys,
  onStep: (step: UploadStep) => void = () => {},
  send: StorageSend = (url, init) => (storageSendOverride ?? fetch)(url, init),
): Promise<string> {
  onStep("presign");
  const presigned = await unwrap(
    api.POST("/api/v1/documents/uploads", {
      headers: { "Idempotency-Key": keys.upload },
      body: {
        filename: file.name,
        content_type: file.type,
        size_bytes: file.size,
        purpose: "evidence",
      },
    }),
  );
  onStep("send");
  const form = new FormData();
  for (const [name, value] of Object.entries(presigned.fields)) form.append(name, value);
  // The file must be the last field of an S3 POST policy form.
  form.append("file", file);
  let stored: Response;
  try {
    stored = await send(presigned.url, { method: "POST", body: form, credentials: "omit" });
  } catch {
    throw new StorageUploadError(0);
  }
  if (!stored.ok) throw new StorageUploadError(stored.status);
  onStep("register");
  const document = await unwrap(
    api.POST("/api/v1/documents", {
      headers: { "Idempotency-Key": keys.register },
      body: { upload_id: presigned.upload_id, title, doc_type: "evidence" },
    }),
  );
  return document.id;
}
