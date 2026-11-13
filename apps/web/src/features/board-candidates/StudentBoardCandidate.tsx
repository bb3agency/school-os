"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Link } from "@/i18n/navigation";
import { useBffClient } from "@/lib/bff/query";
import { IssueList, labelOr, looseT } from "./IssueList";
import { CANDIDATE_KEYS, fetchStudentCandidate, type StudentCandidate } from "./data";

function shown(candidate: StudentCandidate, key: string, t: (key: string) => string): string {
  const scheme = candidate.scheme;
  const value = candidate.values[key];
  if (!value || !scheme) return t("notRecorded");
  if (key === "photo_signature_ready") return value === "yes" ? t("yes") : t("no");
  if (key === "board_subjects") {
    const labels = new Map(scheme.subjects.flatMap((g) => g.options.map((o) => [o.code, o.label])));
    return value
      .split(",")
      .map((c) => `${labels.get(c) ?? c} (${c})`)
      .join(", ");
  }
  const slot = scheme.languages.find((s) => s.slot === key);
  const label = slot?.options.find((o) => o.code === value)?.label;
  return label ? `${label} (${value})` : value;
}

/**
 * The board candidate card on the student profile (FO-10, FR-STU-026): the choices recorded
 * for the student's board class and what is still missing. Nothing when the student's class is
 * not a board class (or the answer cannot be read): the details table still lists the values.
 */
export function StudentBoardCandidate({ studentId }: { studentId: string }) {
  const t = useTranslations("boardCandidates");
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: CANDIDATE_KEYS.student(studentId),
    queryFn: () => fetchStudentCandidate(api, studentId),
    retry: false,
  });
  const candidate = query.data;
  if (!candidate?.scheme) return null;
  const scheme = candidate.scheme;
  const lt = looseT(t);
  const label = (key: string) => labelOr(lt, "field", key);
  const text = (key: string) => lt(key);
  return (
    <Card
      title={t("profileTitle")}
      description={t("profileDescription", { scheme: scheme.label_en })}
      actions={
        candidate.section_id ? (
          <Link href="/students/board-candidates" className="text-sm text-primary underline">
            {t("openGrid")}
          </Link>
        ) : null
      }
    >
      <div className="space-y-3">
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {scheme.keys.map((key) => (
            <div key={key} className="flex flex-wrap gap-x-2">
              <dt className="text-ink-muted">{label(key)}</dt>
              <dd className="text-ink">
                {key === "exam_medium" && candidate.values[key]
                  ? labelOr(lt, "medium", candidate.values[key])
                  : shown(candidate, key, text)}
              </dd>
            </div>
          ))}
        </dl>
        {candidate.ready ? (
          <Badge tone="success">{t("ready")}</Badge>
        ) : (
          <IssueList issues={candidate.issues} scheme={scheme} />
        )}
      </div>
    </Card>
  );
}
