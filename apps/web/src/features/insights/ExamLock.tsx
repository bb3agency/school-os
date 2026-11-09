"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField } from "@/components/ui/Input";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import { zodErrorKeys } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
import {
  KEYS,
  PERM,
  examLock,
  lockExam,
  unlockExam,
  unlockSchema,
  useMarksHistory,
  type Exam,
  type MarkChange,
} from "./data";
import { LoadGate, ProblemList } from "./parts";

const UNLOCK_REASON_ID = "exam-unlock-reason";

/**
 * Lock state of an exam (FT-01; FR-MRK-008..FR-MRK-011). The exam coordinator or principal
 * locks it once results are final; only the principal unlocks it, with a reason (step-up MFA is
 * asked by the BFF client when needed). While locked, nobody can enter or import marks.
 */
export function ExamLockPanel({ exam, section }: { exam: Exam; section: string }) {
  const t = useTranslations("marks.lock");
  const tv = useTranslations("validation");
  const can = useStaffCan();
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const lock = examLock(exam);
  const [error, setError] = useState<unknown>(undefined);
  const [pending, setPending] = useState(false);
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const [done, setDone] = useState<"locked" | "unlocked" | null>(null);

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: KEYS.exams }),
      queryClient.invalidateQueries({ queryKey: KEYS.marks(section, exam.id) }),
      queryClient.invalidateQueries({ queryKey: KEYS.marksHistory(section, exam.id) }),
    ]);
  }

  async function doLock() {
    setError(undefined);
    setDone(null);
    setPending(true);
    try {
      await lockExam(api, exam.id);
      setDone("locked");
      await refresh();
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  async function doUnlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const parsed = unlockSchema.safeParse(Object.fromEntries(new FormData(form)));
    if (!parsed.success) {
      const key = zodErrorKeys(parsed.error).reason ?? "invalid";
      setReasonError(translateOr(tv, key, "invalid"));
      return;
    }
    setReasonError(undefined);
    setError(undefined);
    setDone(null);
    setPending(true);
    try {
      await unlockExam(api, exam.id, parsed.data.reason);
      form.reset();
      setDone("unlocked");
      await refresh();
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const lockedAt = lock.locked_at ? (formatDateTime(lock.locked_at) ?? lock.locked_at) : null;
  return (
    <Card
      title={t("title")}
      description={lock.locked ? t("lockedDescription") : t("openDescription")}
    >
      <div className="space-y-3" data-testid="exam-lock">
        <p className="flex flex-wrap items-center gap-2 text-sm text-ink">
          {lock.locked ? (
            <Badge tone="warning">{t("lockedBadge")}</Badge>
          ) : (
            <Badge tone="neutral">{t("openBadge")}</Badge>
          )}
          {lock.locked && lockedAt ? <span>{t("lockedAt", { when: lockedAt })}</span> : null}
          {!lock.locked && lock.unlock_reason ? (
            <span className="break-words">
              {t("unlockedBecause", { reason: lock.unlock_reason })}
            </span>
          ) : null}
        </p>
        {done === "locked" ? (
          <Alert tone="success" live>
            {t("lockedDone")}
          </Alert>
        ) : null}
        {done === "unlocked" ? (
          <Alert tone="success" live>
            {t("unlockedDone")}
          </Alert>
        ) : null}
        {!lock.locked && can(PERM.examManage) ? (
          <div className="space-y-2">
            <p className="text-sm text-ink-muted">{t("lockConsequence")}</p>
            <Button
              variant="secondary"
              disabled={pending}
              aria-disabled={pending || undefined}
              onClick={() => void doLock()}
            >
              {t("lock")}
            </Button>
          </div>
        ) : null}
        {lock.locked && can(PERM.examUnlock) ? (
          <form onSubmit={(event) => void doUnlock(event)} noValidate className="space-y-2">
            <TextAreaField
              id={UNLOCK_REASON_ID}
              name="reason"
              label={t("reason")}
              hint={t("reasonHint")}
              maxLength={500}
              rows={2}
              error={reasonError}
            />
            <Button
              type="submit"
              variant="secondary"
              disabled={pending}
              aria-disabled={pending || undefined}
            >
              {t("unlock")}
            </Button>
          </form>
        ) : null}
        {lock.locked && !can(PERM.examUnlock) ? (
          <p className="text-sm text-ink-muted">{t("askPrincipal")}</p>
        ) : null}
        <ApiErrorAlert error={error} />
        <ProblemList error={error} />
      </div>
    </Card>
  );
}

function value(
  t: ReturnType<typeof useTranslations>,
  marks: MarkChange["new_marks"],
  max: MarkChange["new_max_marks"],
  absent: boolean,
): string {
  if (absent) return t("absent");
  return `${Number(marks)}/${Number(max)}`;
}

/** Every change of the section's marks for the exam, newest first (FR-MRK-013). */
export function MarksHistoryCard({ section, exam }: { section: string; exam: string }) {
  const t = useTranslations("marks.history");
  const [open, setOpen] = useState(false);
  const history = useMarksHistory(section, exam, open);
  return (
    <Card padding="sm">
      <details onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary className="cursor-pointer text-base font-semibold text-ink">{t("title")}</summary>
        <p className="mt-1 text-sm text-ink-muted">{t("description")}</p>
        {open ? (
          <div className="mt-3">
            <LoadGate data={history}>
              {(data) =>
                data.changes.length === 0 ? (
                  <p className="text-sm text-ink-muted">{t("empty")}</p>
                ) : (
                  <TableScroll label={t("title")}>
                    <Table density="compact">
                      <THead>
                        <Tr>
                          <Th>{t("columns.student")}</Th>
                          <Th>{t("columns.subject")}</Th>
                          <Th numeric>{t("columns.before")}</Th>
                          <Th numeric>{t("columns.after")}</Th>
                          <Th>{t("columns.who")}</Th>
                          <Th>{t("columns.when")}</Th>
                          <Th>{t("columns.reason")}</Th>
                        </Tr>
                      </THead>
                      <TBody>
                        {data.changes.map((change, index) => (
                          <Tr key={`${change.student.student_id}:${change.changed_at}:${index}`}>
                            <Td>
                              <span className="break-words">
                                {change.student.full_name ?? t("unnamed")}
                              </span>
                            </Td>
                            <Td>{change.subject}</Td>
                            <Td numeric>
                              <span className="num">
                                {value(
                                  t,
                                  change.previous_marks,
                                  change.previous_max_marks,
                                  change.previous_absent,
                                )}
                              </span>
                            </Td>
                            <Td numeric>
                              <span className="num">
                                {value(
                                  t,
                                  change.new_marks,
                                  change.new_max_marks,
                                  change.new_absent,
                                )}
                              </span>
                            </Td>
                            <Td>{change.changed_by_name ?? t("formerStaff")}</Td>
                            <Td>
                              <span className="num">
                                {formatDateTime(change.changed_at) ?? change.changed_at}
                              </span>
                            </Td>
                            <Td>
                              <span className="flex flex-wrap items-center gap-1 break-words">
                                {change.after_lock ? (
                                  <Badge tone="warning">{t("afterLock")}</Badge>
                                ) : null}
                                {change.reason ?? ""}
                              </span>
                            </Td>
                          </Tr>
                        ))}
                      </TBody>
                    </Table>
                  </TableScroll>
                )
              }
            </LoadGate>
          </div>
        ) : null}
      </details>
    </Card>
  );
}
