"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useEffect, useId, useState, type KeyboardEvent } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { cn } from "@/lib/cn";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";

export type SubjectKind = "student" | "guardian" | "member";
export const SUBJECT_KINDS: readonly SubjectKind[] = ["student", "guardian", "member"];

export interface PickedStudent {
  id: string;
  label: string;
}

/** GET /staff needs one of these (display names and role keys only). */
const STAFF_LIST = ["tenant.structure.manage", "user.manage"];

/**
 * Who the request is about: a student (search combobox, `POST /students/search`: the name goes
 * in a body, never a URL), one of a student's parents or guardians, or a staff member. The
 * chosen id is submitted in a hidden or select field named `student_id`, `guardian_id` or
 * `membership_id`.
 */
export function SubjectPicker({
  kind,
  onKindChange,
  error,
  fieldId,
}: {
  kind: SubjectKind;
  onKindChange: (kind: SubjectKind) => void;
  error?: string | undefined;
  fieldId: string;
}) {
  const t = useTranslations("privacy.new.subject");
  const [student, setStudent] = useState<PickedStudent | null>(null);
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm font-semibold text-ink">{t("legend")}</legend>
      <SegmentedControl
        legend={t("kindLegend")}
        value={kind}
        onValueChange={(value) => {
          const next = SUBJECT_KINDS.find((item) => item === value);
          if (next) onKindChange(next);
        }}
        options={SUBJECT_KINDS.map((value) => ({ value, label: t(`kinds.${value}`) }))}
        block="phone"
      />
      {kind === "member" ? (
        <StaffSelect fieldId={fieldId} error={error} />
      ) : (
        <>
          {student ? (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-muted px-3 py-2">
              <span className="min-w-0 font-semibold break-words text-ink">{student.label}</span>
              {kind === "student" ? (
                <input type="hidden" name="student_id" value={student.id} />
              ) : null}
              <Button variant="ghost" size="sm" onClick={() => setStudent(null)}>
                {t("change")}
              </Button>
            </div>
          ) : (
            <StudentCombobox
              fieldId={fieldId}
              error={kind === "student" ? error : undefined}
              onPick={setStudent}
            />
          )}
          {kind === "guardian" ? (
            student ? (
              <GuardianSelect studentId={student.id} fieldId={fieldId} error={error} />
            ) : error ? (
              <p id={`${fieldId}-error`} className="text-sm font-semibold text-danger">
                {error}
              </p>
            ) : (
              <p className="text-sm text-ink-muted">{t("guardianFirst")}</p>
            )
          ) : null}
        </>
      )}
    </fieldset>
  );
}

function GuardianSelect({
  studentId,
  fieldId,
  error,
}: {
  studentId: string;
  fieldId: string;
  error?: string | undefined;
}) {
  const t = useTranslations("privacy.new.subject");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const guardians = useQuery({
    queryKey: ["staff", "students", studentId, "guardians"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/students/{student_id}/guardians", {
          params: { path: { student_id: studentId } },
        }),
      ),
    retry: false,
  });
  if (guardians.isError) return <ApiErrorAlert error={guardians.error} />;
  const options = (guardians.data ?? []).map((guardian) => ({
    value: guardian.id,
    label: guardian.full_name,
  }));
  return (
    <SelectField
      id={fieldId}
      name="guardian_id"
      label={t("guardian")}
      error={error}
      defaultValue=""
      placeholder={guardians.isPending ? tc("loading") : tc("chooseOne")}
      options={options}
    />
  );
}

function StaffSelect({ fieldId, error }: { fieldId: string; error?: string | undefined }) {
  const t = useTranslations("privacy.new.subject");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const allowed = can(STAFF_LIST);
  const staff = useQuery({
    queryKey: ["staff", "staff-list"],
    queryFn: () => unwrap(api.GET("/api/v1/staff", { params: { query: { limit: 200 } } })),
    enabled: allowed,
    retry: false,
  });
  if (!allowed) return <p className="text-sm text-ink-muted">{t("staffNotAllowed")}</p>;
  if (staff.isError) return <ApiErrorAlert error={staff.error} />;
  return (
    <SelectField
      id={fieldId}
      name="membership_id"
      label={t("member")}
      error={error}
      defaultValue=""
      placeholder={staff.isPending ? tc("loading") : tc("chooseOne")}
      options={(staff.data?.data ?? []).map((member) => ({
        value: member.membership_id,
        label: member.display_name,
      }))}
    />
  );
}

/** Student search as a combobox with a listbox of up to 8 matches (WAI-ARIA combobox). */
function StudentCombobox({
  fieldId,
  error,
  onPick,
}: {
  fieldId: string;
  error?: string | undefined;
  onPick: (student: PickedStudent) => void;
}) {
  const t = useTranslations("privacy.new.subject");
  const api = useBffClient("staff");
  const listId = useId();
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<{ query: string; rows: PickedStudent[] } | null>(null);
  const [active, setActive] = useState(0);
  const [failure, setFailure] = useState<unknown>(undefined);
  const text = query.trim();

  useEffect(() => {
    if (text.length < 2) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      // Names go in the body, never in a URL (SEC-008).
      unwrap(api.POST("/api/v1/students/search", { body: { query: text, limit: 8 } }))
        .then((page) => {
          if (cancelled) return;
          setFound({
            query: text,
            rows: page.data.map((row) => ({
              id: row.id,
              label: [row.display_name ?? t("noName"), row.admission_no, row.class_section]
                .filter(Boolean)
                .join(" · "),
            })),
          });
          setActive(0);
          setFailure(undefined);
        })
        .catch((problem: unknown) => {
          if (!cancelled) setFailure(problem);
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [api, text, t]);

  const current = text.length >= 2 && found?.query === text ? found.rows : [];
  const searching = text.length >= 2 && found?.query !== text && !failure;

  function choose(student: PickedStudent | undefined) {
    if (!student) return;
    onPick(student);
    setQuery("");
    setFound(null);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" && current.length) {
      event.preventDefault();
      setActive((i) => (i + 1) % current.length);
    } else if (event.key === "ArrowUp" && current.length) {
      event.preventDefault();
      setActive((i) => (i - 1 + current.length) % current.length);
    } else if (event.key === "Enter") {
      // Enter picks a match; it never submits the whole form from the search box.
      event.preventDefault();
      if (current.length) choose(current[active]);
    } else if (event.key === "Escape") {
      setQuery("");
    }
  }

  const open = current.length > 0;
  const describedBy = [`${fieldId}-hint`, error ? `${fieldId}-error` : null]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="relative space-y-1">
      <label htmlFor={fieldId} className="text-sm font-semibold text-ink">
        {t("searchLabel")}
      </label>
      <p id={`${fieldId}-hint`} className="text-sm text-ink-muted">
        {t("searchHint")}
      </p>
      <div className="relative">
        <Icon
          name="search"
          className="pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-ink-muted"
        />
        <Input
          id={fieldId}
          role="combobox"
          aria-expanded={open}
          aria-controls={`${listId}-list`}
          aria-autocomplete="list"
          aria-describedby={describedBy}
          aria-invalid={error ? true : undefined}
          aria-activedescendant={open ? `${listId}-opt-${active}` : undefined}
          autoComplete="off"
          className="ps-9"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onKeyDown}
          maxLength={100}
        />
      </div>
      {searching ? <p className="text-sm text-ink-muted">{t("searching")}</p> : null}
      {text.length >= 2 && !searching && !open && !failure ? (
        <p className="text-sm text-ink-muted" role="status">
          {t("noMatches")}
        </p>
      ) : null}
      <div
        id={`${listId}-list`}
        role="listbox"
        aria-label={t("matches")}
        hidden={!open}
        className="overlay-panel absolute z-10 mt-1 w-full p-1"
      >
        {current.map((row, i) => (
          <div
            key={row.id}
            id={`${listId}-opt-${i}`}
            role="option"
            tabIndex={-1}
            aria-selected={i === active}
            className={cn(
              "min-h-6 cursor-pointer rounded-md px-3 py-2 break-words",
              i === active ? "bg-accent-soft" : "hover:bg-surface-muted",
            )}
            onMouseDown={(event) => {
              event.preventDefault();
              choose(row);
            }}
          >
            {row.label}
          </div>
        ))}
      </div>
      {error ? (
        <p id={`${fieldId}-error`} className="text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <ApiErrorAlert error={failure} />
    </div>
  );
}
