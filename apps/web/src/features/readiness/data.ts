"use client";

import { useQuery } from "@tanstack/react-query";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useProfiles } from "@/features/findings/data";

/** Query-key roots; a finished run invalidates them by prefix. */
export const READINESS_KEYS = {
  all: ["staff", "dq", "readiness"],
  summary: (profile: string) => ["staff", "dq", "readiness", profile, "summary"] as const,
  section: (profile: string, section: string) =>
    ["staff", "dq", "readiness", profile, "section", section] as const,
  student: (profile: string, student: string) =>
    ["staff", "dq", "readiness", profile, "student", student] as const,
} as const;

/** Profiles that have a readiness check (AP SSC 2027, APAAR …), from GET /dq/profiles. */
export function useReadinessProfiles() {
  const profiles = useProfiles();
  return {
    ...profiles,
    data: profiles.data?.filter((profile) => profile.readiness === true),
  };
}

export function useReadinessSummary(profile: string) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: READINESS_KEYS.summary(profile),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/dq/readiness/{profile_key}", {
          params: { path: { profile_key: profile } },
        }),
      ),
    retry: false,
  });
}

export function useSectionReadiness(profile: string, sectionId: string | null) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: READINESS_KEYS.section(profile, sectionId ?? ""),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/dq/readiness/{profile_key}/students", {
          params: { path: { profile_key: profile }, query: { section_id: sectionId ?? "" } },
        }),
      ),
    enabled: sectionId !== null,
    retry: false,
  });
}

export function useStudentReadiness(profile: string, studentId: string) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: READINESS_KEYS.student(profile, studentId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/dq/readiness/{profile_key}/students/{student_id}", {
          params: { path: { profile_key: profile, student_id: studentId } },
        }),
      ),
    retry: false,
  });
}
