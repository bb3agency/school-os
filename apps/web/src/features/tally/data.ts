"use client";

import type { components } from "@schoolos/api-client";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError, toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * Tally connector (M6; ADR-0032 Proposed; behind the per-school flag `tally.connector.enabled`).
 * Every route answers 404 while the flag is off: the screens then say the connector is not
 * switched on. Ledger names and balances are personal financial data (C2, `finance.read`):
 * they are shown on these screens only and never go into a URL (searches are POST bodies).
 * The API checks every permission; these values only decide what the screens offer.
 */

export const FINANCE_READ = "finance.read";
export const DEVICE_MANAGE = "tally.device.manage";
export const CONFIGURE = "tally.configure";
export const TALLY_PERMISSIONS = [FINANCE_READ, DEVICE_MANAGE, CONFIGURE] as const;

type S = components["schemas"];
export type ConnectorStatus = S["ConnectorStatus"];
export type Device = S["DeviceOut"];
export type EnrolmentCode = S["EnrolmentCodeOut"];
export type Group = S["GroupOut"];
export type Party = S["PartyOut"];
export type PartyDetail = S["PartyDetail"];
export type LinkedStudent = S["LinkedStudentOut"];
export type DuesPage = S["DuesPage"];
export type StudentDues = S["StudentDuesOut"];
export type LinkFilter = "all" | "linked" | "unlinked";

export interface Paged<T> {
  data: T[];
  next_cursor: string | null;
}

export const KEYS = {
  all: ["staff", "tally"] as const,
  status: ["staff", "tally", "status"] as const,
  devices: ["staff", "tally", "devices"] as const,
  groups: ["staff", "tally", "groups"] as const,
  parties: (filter: LinkFilter, query: string, cursor: string | null) =>
    ["staff", "tally", "parties", filter, query, cursor] as const,
  party: (id: string) => ["staff", "tally", "party", id] as const,
  dues: (cursor: string | null) => ["staff", "tally", "dues", cursor] as const,
} as const;

/** `W/"3"` for If-Match (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

function retry(count: number, error: unknown): boolean {
  return (
    count < 1 &&
    !(error instanceof NotAvailableError) &&
    !(error instanceof AuthRedirectError) &&
    !(error instanceof ApiError && error.status < 500)
  );
}

/** True when a failed load means "the connector is not switched on for this school" (404). */
export function isConnectorOff(loadable: Loadable<unknown>): boolean {
  return loadable.status === "error" && loadable.reason === "not_found";
}

export function useConnectorStatus(enabled: boolean): Loadable<ConnectorStatus> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.status,
    queryFn: () => unwrap(api.GET("/api/v1/tally/status")),
    enabled,
    retry,
    staleTime: 30_000,
  });
  return toLoadable(query);
}

export function useDevices(enabled: boolean): Loadable<Device[]> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.devices,
    queryFn: () => unwrap(api.GET("/api/v1/tally/devices")),
    enabled,
    retry,
  });
  return toLoadable(query);
}

export function useGroups(enabled: boolean): Loadable<Group[]> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.groups,
    queryFn: () => unwrap(api.GET("/api/v1/tally/groups")),
    enabled,
    retry,
  });
  return toLoadable(query);
}

/** Ledgers of the last snapshot; a name filter goes in a POST body, never in the URL. */
export function useParties(
  filter: LinkFilter,
  search: string,
  cursor: string | null,
  enabled: boolean,
): Loadable<Paged<Party>> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.parties(filter, search, cursor),
    queryFn: () => {
      const params = { query: { limit: 50, ...(cursor ? { cursor } : {}) } };
      return search
        ? unwrap(
            api.POST("/api/v1/tally/parties/search", {
              params,
              body: { query: search, link: filter },
            }),
          )
        : unwrap(
            api.GET("/api/v1/tally/parties", {
              params: { query: { ...params.query, link: filter } },
            }),
          );
    },
    enabled,
    retry,
  });
  return toLoadable(query);
}

export function useParty(id: string | null, enabled: boolean): Loadable<PartyDetail> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.party(id ?? "none"),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tally/parties/{party_id}", {
          params: { path: { party_id: id ?? "" } },
        }),
      ),
    enabled: enabled && id !== null,
    retry,
  });
  return toLoadable(query);
}

export function useDues(cursor: string | null, enabled: boolean): Loadable<DuesPage> {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: KEYS.dues(cursor),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tally/dues", {
          params: { query: { limit: 50, ...(cursor ? { cursor } : {}) } },
        }),
      ),
    enabled,
    retry,
  });
  return toLoadable(query);
}

/** "Accounts PC": a label for the office PC, shown on the device list. */
export const codeSchema = z.object({
  device_name: z.string().trim().min(1, { error: "required" }).max(80, { error: "tooLong" }),
});

/** The command the IT person runs on the office PC (as the service account). */
export function enrolCommand(origin: string, tenantId: string, code: string): string {
  return `sos-tally-agent enrol --server ${origin} --school ${tenantId} --code ${code}`;
}

/** An amount is due (positive), an advance (negative) or settled (zero). */
export function balanceKind(amount: string): "due" | "advance" | "settled" {
  const value = Number(amount);
  if (!Number.isFinite(value) || value === 0) return "settled";
  return value > 0 ? "due" : "advance";
}
