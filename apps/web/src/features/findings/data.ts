"use client";

import type { AcademicYear, SchoolClass, Section } from "@schoolos/api-client";
import { useQuery } from "@tanstack/react-query";
import { useLocale } from "next-intl";
import { useMemo } from "react";
import { unwrap, useBffClient } from "@/lib/bff/query";
import type { AttributeDef, DqProfile, DqRule } from "./types";

/** Query-key roots; mutations invalidate by these prefixes. */
export const DQ_KEYS = {
  findings: ["staff", "dq", "findings"],
  finding: (id: string) => ["staff", "dq", "findings", "one", id] as const,
  summary: ["staff", "dq", "summary"],
  rules: ["staff", "dq", "rules"],
  profiles: ["staff", "dq", "profiles"],
  run: (id: string) => ["staff", "dq", "runs", id] as const,
  attributes: ["staff", "attributes"],
  structure: ["staff", "structure", "dq"],
} as const;

const CATALOG_STALE_MS = 10 * 60_000;

/** Rule catalog DQ-001..DQ-012, DQ-021, DQ-022 (texts from the API). */
export function useRules() {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: DQ_KEYS.rules,
    queryFn: () => unwrap(api.GET("/api/v1/dq/rules")),
    staleTime: CATALOG_STALE_MS,
    retry: false,
  });
}

/** Export pre-check profiles (CISCE registration, UDISE+ …). */
export function useProfiles() {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: DQ_KEYS.profiles,
    queryFn: () => unwrap(api.GET("/api/v1/dq/profiles")),
    staleTime: CATALOG_STALE_MS,
    retry: false,
  });
}

/** Student attributes (labels, identity flag, data type). Needs `student.read_basic`. */
export function useAttributes() {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: DQ_KEYS.attributes,
    queryFn: () => unwrap(api.GET("/api/v1/attributes")),
    staleTime: CATALOG_STALE_MS,
    retry: false,
  });
}

export function attributeLabel(
  attributes: readonly AttributeDef[] | undefined,
  key: string | null,
  locale: string,
): string | null {
  if (!key) return null;
  const found = attributes?.find((item) => item.key === key);
  if (!found) return key;
  return locale === "te" && found.label_te ? found.label_te : found.label_en;
}

export function ruleText(rules: readonly DqRule[] | undefined, id: string, locale: string) {
  const rule = rules?.find((item) => item.id === id);
  if (!rule) return null;
  return locale === "te" && rule.explanation.te ? rule.explanation.te : rule.explanation.en;
}

export function profileLabel(
  profiles: readonly DqProfile[] | undefined,
  key: string | null,
  locale: string,
): string | null {
  if (!key) return null;
  const found = profiles?.find((item) => item.key === key);
  if (!found) return key;
  return locale === "te" && found.label_te ? found.label_te : found.label_en;
}

export interface SectionOption {
  id: string;
  classId: string;
  /** "Class 9 · A" in the reader's language. */
  label: string;
  classLabel: string;
  classOrder: number;
}

const PAGE = { limit: 200 } as const;

/**
 * Sections of the current academic year with their class names, for filters and the "run
 * checks" scope. Scoped staff (class teachers) get only their own sections from the API.
 */
export function useSectionOptions(): {
  sections: readonly SectionOption[];
  loading: boolean;
} {
  const api = useBffClient("staff");
  const locale = useLocale();
  const query = useQuery({
    queryKey: DQ_KEYS.structure,
    queryFn: async () => {
      const [years, classes, sections] = await Promise.all([
        unwrap(api.GET("/api/v1/academic-years", { params: { query: PAGE } })),
        unwrap(api.GET("/api/v1/classes", { params: { query: PAGE } })),
        unwrap(api.GET("/api/v1/sections", { params: { query: PAGE } })),
      ]);
      return { years: years.data, classes: classes.data, sections: sections.data };
    },
    staleTime: CATALOG_STALE_MS,
    retry: false,
  });
  const sections = useMemo(
    () => (query.data ? sectionOptions(query.data, locale) : []),
    [query.data, locale],
  );
  return { sections, loading: query.isPending };
}

export function sectionOptions(
  data: {
    years: readonly AcademicYear[];
    classes: readonly SchoolClass[];
    sections: readonly Section[];
  },
  locale: string,
): SectionOption[] {
  const current = data.years.find((year) => year.is_current)?.id;
  const classes = new Map(data.classes.map((item) => [item.id, item]));
  return data.sections
    .filter((section) => !current || section.academic_year_id === current)
    .map((section) => {
      const schoolClass = classes.get(section.class_id);
      const classLabel = schoolClass
        ? locale === "te" && schoolClass.display_te
          ? schoolClass.display_te
          : schoolClass.display_en
        : "";
      return {
        id: section.id,
        classId: section.class_id,
        label: classLabel ? `${classLabel} · ${section.name}` : section.name,
        classLabel,
        classOrder: schoolClass?.sort_order ?? Number.MAX_SAFE_INTEGER,
      };
    })
    .sort(
      (a, b) =>
        a.classOrder - b.classOrder || a.label.localeCompare(b.label, "en", { numeric: true }),
    );
}
