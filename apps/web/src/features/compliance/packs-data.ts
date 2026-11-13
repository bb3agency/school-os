import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";
import * as z from "@/lib/zod";

/**
 * Inspection readiness packs (FC-07 merged with FO-12; FR-COMP-024..FR-COMP-045): per authority
 * (DEO/MEO, APSERMC, CBSE, fire department; `verified: false`) what SchoolOS holds and what is
 * missing, then "make the pack": the states are kept, the index prints from the API with its own
 * strict CSP, and each item prints from its own screen. `compliance.read` sees; making a pack is
 * `compliance.manage`; choosing registers (student lists) also needs `register.read`.
 *
 * The routes are new in wave F3 and not yet in the generated client: until the lead regenerates
 * `packages/api-client`, the calls go through a loosely typed view of the BFF client and every
 * answer is checked with the zod schemas below (hand-written from `app/compliance/packs_schemas.py`).
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
] as const;
export type ItemState = (typeof ITEM_STATES)[number];

export const SOURCES = [
  "certificate",
  "register",
  "disclosure",
  "privacy_notice",
  "dpdp_readiness",
  "not_tracked",
] as const;
export type ItemSource = (typeof SOURCES)[number];

const itemSchema = z.object({
  key: z.string(),
  label: z.string(),
  source: z.enum(SOURCES),
  state: z.enum(ITEM_STATES),
  fix_by: z.string(),
  note: z.string().nullish(),
  feature: z.string().nullish(),
  kind: z.string().nullish(),
  certificate_id: z.string().nullish(),
  valid_until: z.string().nullish(),
  has_copy: z.boolean().nullish(),
  register_view: z.string().nullish(),
  included: z.boolean().nullish(),
  snapshot_id: z.string().nullish(),
  published_at: z.string().nullish(),
  notice_id: z.string().nullish(),
  issued_on: z.string().nullish(),
  done: z.number().nullish(),
  applicable: z.number().nullish(),
});
export type PackItem = z.output<typeof itemSchema>;

const countsSchema = z.object({
  ready: z.number(),
  attention: z.number(),
  missing: z.number(),
  not_tracked: z.number(),
  left_out: z.number(),
});
export type PackCounts = z.output<typeof countsSchema>;

const readinessPackSchema = z.object({
  key: z.string(),
  label: z.string(),
  authority: z.string(),
  note: z.string(),
  items: z.array(itemSchema),
  counts: countsSchema,
});
export type ReadinessPack = z.output<typeof readinessPackSchema>;

export const readinessSchema = z.object({
  verified: z.boolean(),
  source: z.string(),
  version: z.number(),
  packs: z.array(readinessPackSchema),
});
export type InspectionReadiness = z.output<typeof readinessSchema>;

export const packSchema = z.object({
  id: z.string(),
  pack_key: z.string(),
  pack_label: z.string(),
  authority: z.string(),
  pack_version: z.number(),
  verified: z.boolean(),
  academic_year_id: z.string().nullable(),
  academic_year_label: z.string().nullable(),
  visit_on: z.string().nullable(),
  registers: z.array(z.string()),
  counts: countsSchema,
  generated_at: z.string(),
  generated_by_name: z.string().nullable(),
});
export type InspectionPack = z.output<typeof packSchema>;

export const packDetailSchema = packSchema.extend({ items: z.array(itemSchema) });
export type InspectionPackDetail = z.output<typeof packDetailSchema>;

export interface MakePackInput {
  pack_key: string;
  academic_year_id: string | null;
  visit_on: string | null;
  registers: string[];
}

/** The BFF client seen without the generated path types (routes not generated yet). */
interface LooseClient {
  GET(
    path: string,
    init?: object,
  ): Promise<{ data?: unknown; error?: unknown; response: Response }>;
  POST(
    path: string,
    init?: object,
  ): Promise<{ data?: unknown; error?: unknown; response: Response }>;
}

function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

const BASE = "/api/v1/compliance";

export async function fetchReadiness(api: ApiClient): Promise<InspectionReadiness> {
  return readinessSchema.parse(await unwrap(loose(api).GET(`${BASE}/inspection-readiness`)));
}

export async function fetchPackLog(api: ApiClient): Promise<InspectionPack[]> {
  return z.array(packSchema).parse(await unwrap(loose(api).GET(`${BASE}/inspection-packs`)));
}

export async function makePack(
  api: ApiClient,
  input: MakePackInput,
  idempotencyKey: string,
): Promise<InspectionPackDetail> {
  const body = {
    pack_key: input.pack_key,
    registers: input.registers,
    ...(input.academic_year_id ? { academic_year_id: input.academic_year_id } : {}),
    ...(input.visit_on ? { visit_on: input.visit_on } : {}),
  };
  return packDetailSchema.parse(
    await unwrap(
      loose(api).POST(`${BASE}/inspection-packs`, {
        body,
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    ),
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
