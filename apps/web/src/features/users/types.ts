/**
 * Staff account types from the generated API client (apps/api/openapi.json; docs/09 Identity,
 * US-102, FR-IAM-010..014). Runtime lists carry `satisfies` checks so they fail to compile if
 * the API adds a value. Plain module (no "use client"): server pages may import from it.
 */
import type { components } from "@schoolos/api-client";

type Schemas = components["schemas"];

export type StaffUser = Schemas["UserOut"];
export type StaffRole = Schemas["RoleOut"];
export type StaffScope = Schemas["ScopeOut"];
export type ScopeInput = Schemas["ScopeIn"];
export type InviteInput = Schemas["InviteIn"];
export type MemberStatus = StaffUser["status"];
export type StatusChange = NonNullable<Schemas["UserUpdateIn"]["status"]>;
export type UserLanguage = InviteInput["preferred_language"] & string;

type Exhaustive<T extends string, L extends readonly T[]> = [T] extends [L[number]] ? L : never;

const languages = ["en", "te"] as const satisfies readonly UserLanguage[];
export const USER_LANGUAGES: Exhaustive<UserLanguage, typeof languages> = languages;

/** Permission keys (apps/api/app/authz/permissions.yaml). */
export const USER_PERM = {
  manage: "user.manage",
  assign: "role.assign",
  breakGlass: "breakglass.approve",
} as const;

/**
 * Status changes the API allows (apps/api/app/identity/service.py `_TRANSITIONS`), offered as
 * buttons. `invited → active` is left out on purpose: an invitation becomes active when the
 * person signs in (ADR-0019), not when someone else presses a button.
 */
export const STATUS_ACTIONS: Record<MemberStatus, readonly StatusChange[]> = {
  invited: ["removed"],
  active: ["suspended", "removed"],
  suspended: ["active", "removed"],
  removed: [],
};

/**
 * Roles that need two-step sign-in (roles.yaml `mfa_required`; FR-IAM-002). Only for the
 * "Needs two-step sign-in" hint next to a role: which roles may be given comes from the API
 * (`RoleOut.grantable`), never from rules copied here.
 */
export const MFA_ROLES: readonly string[] = ["owner", "principal", "office_admin"];

/** The temporary SchoolOS support role: changed only on the Support access page (07 §6.4). */
export const BREAKGLASS_ROLE = "platform_support";

/** API bounds (apps/api/app/identity/schemas.py). */
export const MAX_ROLES = 20;
export const MAX_SCOPES = 200;
export const NAME_MAX = 200;
export const EMAIL_MAX = 254;
export const SUBJECT_MAX = 255;
/** Same pattern as the API's `Subject` (the sign-in service's user ID). */
export const SUBJECT_PATTERN = /^[A-Za-z0-9._:@|+=-]+$/;
/** Same pattern as the API's `RoleKey`. */
export const ROLE_KEY_PATTERN = /^[a-z][a-z0-9_]{1,63}$/;

/** `W/"3"` for If-Match from a resource's `version` (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

export function isBreakGlass(user: Pick<StaffUser, "roles">): boolean {
  return user.roles.includes(BREAKGLASS_ROLE);
}
