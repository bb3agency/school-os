"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { useStaffCan } from "@/lib/bff/staff-me";
import { YEAR_END_PERM, useHeldBack } from "./data";

/**
 * FT-08 (FR-YRE-008, FR-YRE-009): on the promotion screen, the students confirmed as detained
 * in the year-end results, offered as held back. Nothing is added without the click, and the
 * person still reviews the list and the preview before committing the promotion. Shown only
 * to holders of `year_result.read`.
 */
export function HeldBackSuggestion({
  yearId,
  held,
  onAdd,
}: {
  yearId: string;
  held: ReadonlySet<string>;
  onAdd: (ids: readonly string[]) => void;
}) {
  const t = useTranslations("yearEnd.promotion");
  const can = useStaffCan();
  const allowed = can(YEAR_END_PERM.read);
  const data = useHeldBack(yearId, allowed);
  const [added, setAdded] = useState(false);
  if (!allowed || data.status !== "ready") return null;
  const found = data.data;
  const missing = found.held_back_student_ids.filter((id) => !held.has(id));
  return (
    <Alert tone={found.no_detention_student_ids.length > 0 ? "warning" : "info"} title={t("title")}>
      <p>{t("body", { count: found.held_back_student_ids.length })}</p>
      {found.unconfirmed > 0 ? <p>{t("unconfirmed", { count: found.unconfirmed })}</p> : null}
      {found.no_detention_student_ids.length > 0 ? (
        <p>{t("noDetention", { count: found.no_detention_student_ids.length })}</p>
      ) : null}
      {missing.length > 0 ? (
        <div className="mt-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              onAdd(missing);
              setAdded(true);
            }}
          >
            {t("add")}
          </Button>
        </div>
      ) : null}
      <span role="status" aria-live="polite" className="block text-sm">
        {added ? t("added") : ""}
      </span>
    </Alert>
  );
}
