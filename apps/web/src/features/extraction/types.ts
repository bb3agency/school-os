import type { components, operations } from "@schoolos/api-client";
import type { BadgeTone } from "@/components/ui/Badge";

/** Register-photo extraction types, all from the generated OpenAPI schema (never hand-written). */
type Schemas = components["schemas"];

export type ExtractionBatch = Schemas["BatchOut"];
export type ExtractionBatchDetail = Schemas["BatchDetail"];
export type ExtractionPage = Schemas["PageOut"];
export type ExtractionItem = Schemas["ItemOut"];
export type ExtractionItemDetail = Schemas["ItemDetail"];
export type ExtractedField = Schemas["FieldOut"];
export type ItemConfirm = Schemas["ItemConfirm"];
export type RejectReason = Schemas["ItemReject"]["reason"];
export type BatchStatus = ExtractionBatch["status"];
export type ItemStatus = ExtractionItem["status"];
export type ImageUnavailable = NonNullable<ExtractionItemDetail["image_unavailable"]>;
type ItemQuery = NonNullable<
  operations["list_items_api_v1_extraction_items_get"]["parameters"]["query"]
>;
export type ItemStatusFilter = NonNullable<ItemQuery["status"]>;

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const rejectReasons = [
  "not_a_student_row",
  "duplicate",
  "unreadable",
  "other",
] as const satisfies readonly RejectReason[];
export const REJECT_REASONS: Exhaustive<RejectReason, typeof rejectReasons> = rejectReasons;

const itemStatuses = [
  "pending_review",
  "confirmed",
  "rejected",
] as const satisfies readonly ItemStatus[];
export const ITEM_STATUSES: Exhaustive<ItemStatus, typeof itemStatuses> = itemStatuses;

export const batchTone: Record<BatchStatus, BadgeTone> = {
  queued: "info",
  processing: "info",
  review: "warning",
  completed: "success",
  failed: "danger",
};

export const itemTone: Record<ItemStatus, BadgeTone> = {
  pending_review: "warning",
  confirmed: "success",
  rejected: "neutral",
};

/** A worker is still reading the photos: poll until it settles. */
export const BATCH_BUSY: ReadonlySet<BatchStatus> = new Set(["queued", "processing"]);

/**
 * Register fields a row may carry, in the order of the admission register's columns
 * (extraction/config.yaml `fields`; PRD §8: tab order follows the paper form).
 */
export const REGISTER_FIELDS = [
  "admission_no",
  "admission_date",
  "full_name",
  "gender",
  "dob",
  "father_name",
  "mother_name",
  "mother_tongue",
  "nationality",
] as const;
export type RegisterField = (typeof REGISTER_FIELDS)[number];

/** Fields the API expects as ISO dates (YYYY-MM-DD); the form shows DD/MM/YYYY. */
export const DATE_FIELDS: ReadonlySet<string> = new Set(["dob", "admission_date"]);

/** FR-IMP-020 as built today: one JPG or PNG per page (PDF answers 422 pdf_not_supported). */
export const PHOTO_EXTENSIONS = ["jpg", "jpeg", "png"] as const;
/** API: 1–50 documents per batch (BatchCreate.document_ids). */
export const MAX_PHOTOS = 50;
/** Documents service default for register scans (SOS_DOCUMENTS_MAX_UPLOAD_BYTES, 25 MB). */
export const MAX_PHOTO_BYTES = 25 * 1024 * 1024;
