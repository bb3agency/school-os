import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * DPDP readiness checklist, notice given at admission and the privacy notice sent on WhatsApp
 * (FC-03, FP-13; FR-DPDP-053..066), through the generated client. Not legal advice.
 */
type Schemas = components["schemas"];

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
] as const satisfies readonly Schemas["ReadinessMarkIn"]["item"][];
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
] as const satisfies readonly ReadinessItem["key"][];
export type ItemKey = (typeof ITEM_KEYS)[number];

export const ADMISSION_CHANNELS = [
  "paper",
  "in_person",
  "school_whatsapp_group",
  "sos_whatsapp",
  "email",
  "other",
] as const satisfies readonly Schemas["AdmissionNoticeIn"]["channel"][];

export type Readiness = Schemas["ReadinessOut"];
export type ReadinessItem = Readiness["items"][number];
export type AdmissionPending = Schemas["AdmissionPendingOut"];
export type WhatsAppPreview = Schemas["WhatsAppPreviewOut"];
export type NoticeDelivery = Schemas["NoticeDeliveryOut"];

export const readinessApi = {
  readiness: (api: ApiClient) => unwrap(api.GET("/api/v1/privacy/readiness")),
  recordMark: (api: ApiClient, body: Schemas["ReadinessMarkIn"], key: string) =>
    unwrap(
      api.POST("/api/v1/privacy/readiness/marks", {
        body,
        headers: { "Idempotency-Key": key },
      }),
    ),
  pending: (api: ApiClient) => unwrap(api.GET("/api/v1/privacy/admission-notices/pending")),
  recordAdmission: (api: ApiClient, body: Schemas["AdmissionNoticeIn"], key: string) =>
    unwrap(
      api.POST("/api/v1/privacy/admission-notices", {
        body,
        headers: { "Idempotency-Key": key },
      }),
    ),
  whatsappPreview: (api: ApiClient, body: Schemas["WhatsAppPreviewIn"]) =>
    unwrap(api.POST("/api/v1/privacy/notices/whatsapp/preview", { body })),
  whatsappSend: (api: ApiClient, body: Schemas["WhatsAppSendIn"], key: string) =>
    unwrap(
      api.POST("/api/v1/privacy/notices/whatsapp", {
        body,
        headers: { "Idempotency-Key": key },
      }),
    ),
  delivery: (api: ApiClient, noticeId: string) =>
    unwrap(
      api.GET("/api/v1/privacy/notices/{notice_id}/delivery", {
        params: { path: { notice_id: noticeId } },
      }),
    ),
};
