"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { unwrap, useBffClient } from "@/lib/bff/query";
import type { StaffMe } from "./types";

/** Query key of GET /me (shared by every school screen that hides actions). */
export const STAFF_ME_KEY = ["staff", "me"] as const;

/** The signed-in staff member in the active school: roles and effective permissions. */
export function useStaffMe(): StaffMe | undefined {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: STAFF_ME_KEY,
    queryFn: () => unwrap(api.GET("/api/v1/me")),
    staleTime: 60_000,
    retry: false,
  });
  return query.data;
}

export interface Permissions {
  /** False until GET /me has answered. */
  loaded: boolean;
  /**
   * True only when /me lists the permission. Actions stay hidden until /me has loaded, so
   * nobody is offered something the server will refuse (UX only: the API checks every call).
   */
  has: (permission: string) => boolean;
}

export function permissionsFrom(list: readonly string[] | null | undefined): Permissions {
  return {
    loaded: Boolean(list),
    has: (permission) => Boolean(list?.includes(permission)),
  };
}

export function useStaffPermissions(): Permissions {
  const me = useStaffMe();
  return useMemo(() => permissionsFrom(me?.permissions), [me]);
}

/** Permission names used by the student, import and extraction screens (authz/permissions.yaml). */
export const PERM = {
  readBasic: "student.read_basic",
  readSensitive: "student.read_sensitive",
  create: "student.create",
  updateNonIdentity: "student.update_nonidentity",
  requestChange: "student.identity_change.request",
  approveChange: "student.identity_change.approve",
  findingsRead: "dq.findings.read",
  importRun: "import.run",
  importCommit: "import.commit",
  documentUpload: "document.upload",
  certificateRead: "certificate.read",
  certificateIssue: "certificate.issue",
  certificateApprove: "certificate.approve",
  insightsRead: "insights.read",
  apaarRead: "apaar.consent.read",
  apaarRecord: "apaar.consent.record",
} as const;
