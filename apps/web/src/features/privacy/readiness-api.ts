import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * DPDP readiness checklist, notice given at admission and the privacy notice sent on WhatsApp
 * (FC-03, FP-13; FR-DPDP-053..066). Not legal advice.
 *
 * LEAD: these routes are new in this wave and not yet in the generated client. The calls go
 * through the same BFF client (CSRF, step-up, locale) with an untyped path, and the answers
 * are checked with the zod schemas below. After `make openapi`, switch to the typed
 * `api.GET(...)` and the generated types and drop `untyped()` and the schemas.
 */
export const READINESS_KEYS = {
  readiness: ["staff", "privacy", "readiness"] as const,
  pending: ["staff", "privacy", "admission-pending"] as const,
  delivery: (id: string) => ["staff", "privacy", "notice-delivery", id] as const,
};

export const MARK_ITEMS = [
  "dpa_signed",
  "dpia_done",
  "breach_contact",
  "retention_reviewed",
] as const;
export type MarkItem = (typeof MARK_ITEMS)[number];

export const ITEM_KEYS = [
  "privacy_contact",
  "notices",
  "dpa_signed",
  "dpia_done",
  "test_request",
  "breach_contact",
  "retention_reviewed",
  "consent_forms",
] as const;
export type ItemKey = (typeof ITEM_KEYS)[number];

export const ADMISSION_CHANNELS = [
  "paper",
  "in_person",
  "school_whatsapp_group",
  "sos_whatsapp",
  "email",
  "other",
] as const;

const detailValue = z.union([z.string(), z.number(), z.boolean()]).nullable();

export const readinessSchema = z.object({
  not_legal_advice: z.literal(true),
  done: z.number(),
  applicable: z.number(),
  items: z.array(
    z.object({
      key: z.enum(ITEM_KEYS),
      status: z.enum(["done", "todo", "not_applicable"]),
      detail: z.record(z.string(), detailValue),
      mark: z
        .object({
          item: z.enum(MARK_ITEMS),
          done_on: z.string(),
          document_id: z.string().nullable(),
          recorded_by_name: z.string().nullable(),
          recorded_at: z.string(),
        })
        .nullable(),
      source: z.string(),
    }),
  ),
  admission: z.object({
    since: z.string().nullable(),
    added_since: z.number(),
    without_notice: z.number(),
  }),
});
export type Readiness = z.infer<typeof readinessSchema>;
export type ReadinessItem = Readiness["items"][number];

const studentRow = {
  student_id: z.string(),
  display_name: z.string().nullable(),
  admission_no: z.string().nullable(),
  class_section: z.string().nullable(),
};

export const pendingSchema = z.object({
  since: z.string().nullable(),
  without_notice: z.number(),
  hidden: z.number(),
  items: z.array(z.object({ ...studentRow, added_on: z.string() })),
});
export type AdmissionPending = z.infer<typeof pendingSchema>;

export const recordedSchema = z.object({ recorded: z.number(), already_recorded: z.number() });

const previewSchema = z.object({
  preview_token: z.string(),
  recipients: z.number(),
  guardians: z.number(),
  skipped: z.record(z.string(), z.number()),
  estimated_cost_inr: z.string(),
  blocked_reason: z.string().nullable(),
  section_ids: z.array(z.string()),
});

export const whatsappPreviewSchema = z.object({
  parent_notice_id: z.string(),
  template_version: z.string(),
  title: z.string(),
  body: z.string(),
  preview: previewSchema,
});
export type WhatsAppPreview = z.infer<typeof whatsappPreviewSchema>;

export const sentNoticeSchema = z.object({ id: z.string(), broadcast_id: z.string().nullable() });

export const deliverySchema = z.object({
  notice: z.object({ id: z.string(), issued_on: z.string(), template_version: z.string() }),
  broadcast_id: z.string(),
  guardians: z.record(z.string(), z.number()),
  students: z.number(),
  students_not_reached: z.number(),
  hidden: z.number(),
  not_reached: z.array(z.object(studentRow)),
});
export type NoticeDelivery = z.infer<typeof deliverySchema>;

type UntypedInit = {
  params?: { path?: Record<string, string> };
  body?: unknown;
  headers?: Record<string, string>;
};
type UntypedResult = Promise<{ data?: unknown; error?: unknown; response: Response }>;
interface UntypedClient {
  GET(path: string, init?: UntypedInit): UntypedResult;
  POST(path: string, init?: UntypedInit): UntypedResult;
}

function untyped(api: ApiClient): UntypedClient {
  return api as unknown as UntypedClient;
}

async function call<T>(pending: UntypedResult, schema: z.ZodType<T>): Promise<T> {
  return schema.parse(await unwrap(pending));
}

export const readinessApi = {
  readiness: (api: ApiClient) =>
    call(untyped(api).GET("/api/v1/privacy/readiness"), readinessSchema),
  recordMark: (
    api: ApiClient,
    body: { item: MarkItem; done_on: string; document_id?: string },
    key: string,
  ) =>
    call(
      untyped(api).POST("/api/v1/privacy/readiness/marks", {
        body,
        headers: { "Idempotency-Key": key },
      }),
      z.object({ item: z.enum(MARK_ITEMS) }),
    ),
  pending: (api: ApiClient) =>
    call(untyped(api).GET("/api/v1/privacy/admission-notices/pending"), pendingSchema),
  recordAdmission: (
    api: ApiClient,
    body: { student_ids: string[]; given_on: string; channel: string },
    key: string,
  ) =>
    call(
      untyped(api).POST("/api/v1/privacy/admission-notices", {
        body,
        headers: { "Idempotency-Key": key },
      }),
      recordedSchema,
    ),
  whatsappPreview: (api: ApiClient, body: { parent_notice_id?: string }) =>
    call(
      untyped(api).POST("/api/v1/privacy/notices/whatsapp/preview", { body }),
      whatsappPreviewSchema,
    ),
  whatsappSend: (
    api: ApiClient,
    body: { parent_notice_id: string; preview_token: string; section_ids: string[] },
    key: string,
  ) =>
    call(
      untyped(api).POST("/api/v1/privacy/notices/whatsapp", {
        body,
        headers: { "Idempotency-Key": key },
      }),
      sentNoticeSchema,
    ),
  delivery: (api: ApiClient, noticeId: string) =>
    call(
      untyped(api).GET("/api/v1/privacy/notices/{notice_id}/delivery", {
        params: { path: { notice_id: noticeId } },
      }),
      deliverySchema,
    ),
};
