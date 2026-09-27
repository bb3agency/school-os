/**
 * Change-request types from the generated API client (apps/api/openapi.json). Runtime lists
 * carry `satisfies` checks so they fail to compile if the API adds a value.
 */
import type { components } from "@schoolos/api-client";
import type { BadgeTone } from "@/components/ui/Badge";

type Schemas = components["schemas"];

export type ChangeRequest = Schemas["ChangeRequestOut"];
export type ChangeRequestCreate = Schemas["ChangeRequestCreate"];
export type ChangeRequestStatus = ChangeRequest["status"];
export type StudentSummary = Schemas["StudentSummary"];
export type StudentDetail = Schemas["StudentOut"];
export type UploadPurpose = Schemas["UploadCreate"]["purpose"];

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const statuses = [
  "pending",
  "approved",
  "rejected",
  "expired",
  "cancelled",
] as const satisfies readonly ChangeRequestStatus[];
export const CHANGE_REQUEST_STATUSES: Exhaustive<ChangeRequestStatus, typeof statuses> = statuses;

export const changeRequestTone: Record<ChangeRequestStatus, BadgeTone> = {
  pending: "warning",
  approved: "success",
  rejected: "danger",
  expired: "neutral",
  cancelled: "neutral",
};

/** Permission keys (apps/api/app/authz/permissions.yaml). */
export const CR_REQUEST = "student.identity_change.request";
export const CR_APPROVE = "student.identity_change.approve";

/** Reason and decision note bounds (apps/api/app/changes/config.yaml; DB CHECKs). */
export const TEXT_MIN = 10;
export const TEXT_MAX = 1000;

/** `W/"3"` for If-Match from a resource's `version` (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}
