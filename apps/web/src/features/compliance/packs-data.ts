import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

type Schemas = components["schemas"];

/**
 * Inspection readiness packs (FC-07 merged with FO-12; FR-COMP-024..FR-COMP-045): per authority
 * (DEO/MEO, APSERMC, CBSE, fire department; `verified: false`) what SchoolOS holds and what is
 * missing, then "make the pack": the states are kept, the index prints from the API with its own
 * strict CSP, and each item prints from its own screen. `compliance.read` sees; making a pack is
 * `compliance.manage`; choosing registers (student lists) also needs `register.read`.
 */

export const PACKS_KEYS = {
  readiness: ["staff", "compliance", "inspection-readiness"] as const,
  log: ["staff", "compliance", "inspection-packs"] as const,
};
export const REGISTER_READ = "register.read";

export const ITEM_STATES = [
  "ready",
  "expiring",
  "no_copy",
  "incomplete",
  "expired",
  "missing",
  "left_out",
  "not_tracked",
] as const satisfies readonly Schemas["InspectionPackItemOut"]["state"][];
export type ItemState = Schemas["InspectionPackItemOut"]["state"];

export const SOURCES = [
  "certificate",
  "register",
  "disclosure",
  "privacy_notice",
  "dpdp_readiness",
  "not_tracked",
] as const satisfies readonly Schemas["InspectionPackItemOut"]["source"][];
export type ItemSource = Schemas["InspectionPackItemOut"]["source"];

export type PackItem = Schemas["InspectionPackItemOut"];
export type PackCounts = Schemas["InspectionPackCountsOut"];
export type ReadinessPack = Schemas["InspectionPackReadinessOut"];
export type InspectionReadiness = Schemas["InspectionReadinessOut"];
export type InspectionPack = Schemas["InspectionPackOut"];
export type InspectionPackDetail = Schemas["InspectionPackDetailOut"];

export interface MakePackInput {
  pack_key: string;
  academic_year_id: string | null;
  visit_on: string | null;
  registers: string[];
}

const BASE = "/api/v1/compliance" as const;

export async function fetchReadiness(api: ApiClient): Promise<InspectionReadiness> {
  return unwrap(api.GET(`${BASE}/inspection-readiness`));
}

export async function fetchPackLog(api: ApiClient): Promise<InspectionPack[]> {
  return unwrap(api.GET(`${BASE}/inspection-packs`));
}

export async function makePack(
  api: ApiClient,
  input: MakePackInput,
  idempotencyKey: string,
): Promise<InspectionPackDetail> {
  const body: Schemas["InspectionPackCreate"] = {
    pack_key: input.pack_key,
    registers: input.registers,
    ...(input.academic_year_id ? { academic_year_id: input.academic_year_id } : {}),
    ...(input.visit_on ? { visit_on: input.visit_on } : {}),
  };
  return unwrap(
    api.POST(`${BASE}/inspection-packs`, {
      body,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
}

/** The index print page, opened through the BFF in a new tab (its own CSP, proxy.ts). */
export function indexHref(packId: string): string {
  return `/bff${BASE}/inspection-packs/${encodeURIComponent(packId)}/index`;
}

/** Where each item is printed from (the screen that holds it). */
export function itemHref(item: PackItem): string | null {
  switch (item.source) {
    case "certificate":
      return "/compliance";
    case "register":
      return "/registers";
    case "disclosure":
      return "/settings/disclosure";
    case "privacy_notice":
      return "/privacy/notices";
    case "dpdp_readiness":
      return "/privacy/readiness";
    default:
      return null;
  }
}

/** The register items of a pack, for the "choose registers" list. */
export function registerItems(pack: ReadinessPack): PackItem[] {
  return pack.items.filter((item) => item.source === "register");
}
