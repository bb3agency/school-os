"use client";

/*
 * Student search for the command palette (D72, docs/17 §5.9, invariant 3).
 *
 * The scoped POST /students/search: the API answers with the caller's reach only (a class
 * teacher sees their sections), so the palette never widens what a person can see. The name
 * travels in the body, never in a URL (SEC-008). Rows open the student page; they are kept
 * only while the palette is open (never in "Recent" or browser storage).
 */

import { useTranslations } from "next-intl";
import { useMemo } from "react";
import {
  SEARCH_MAX_RESULTS,
  type CommandItem,
  type PaletteSearch,
} from "@/components/console/CommandPalette";
import { unwrap, useBffClient } from "@/lib/bff/query";
import type { SessionKind } from "@/lib/bff/session-client";

/**
 * The palette's student search for a session of `kind`, or undefined when `kind` is null
 * (the console has no student search, or the person cannot read students).
 */
export function useStudentPaletteSearch(kind: SessionKind | null): PaletteSearch | undefined {
  const t = useTranslations("ui.palette");
  const api = useBffClient(kind ?? "staff");
  return useMemo(() => {
    if (kind === null) return undefined;
    return {
      group: t("groupStudents"),
      messages: {
        loading: t("studentsLoading"),
        empty: t("studentsEmpty"),
        error: t("studentsError"),
        offline: t("studentsOffline"),
      },
      run: async (query: string, signal: AbortSignal): Promise<CommandItem[]> => {
        const page = await unwrap(
          api.POST("/api/v1/students/search", {
            body: { query, limit: SEARCH_MAX_RESULTS },
            signal,
          }),
        );
        return page.data.map((row): CommandItem => ({
          id: `student:${row.id}`,
          label: row.display_name ?? t("studentNoName"),
          group: t("groupStudents"),
          href: `/students/${row.id}`,
          hint: [row.class_section, row.admission_no].filter(Boolean).join(" · "),
          icon: "users",
        }));
      },
    };
  }, [api, kind, t]);
}
