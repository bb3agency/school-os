"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { DialogCloseButton } from "@/components/ui/DialogCloseButton";
import { SearchInput } from "@/components/ui/Input";
import { useSchoolMembers, useSchoolRoles } from "@/features/documents/data";
import { useSectionOptions, type SectionOption } from "@/features/findings/data";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { useDialogClose } from "@/lib/dialog-motion";
import { formatCount } from "@/lib/format";
import {
  countAffected,
  isEmptySelection,
  MAX_PICKED,
  type AffectedCountRequest,
  type AffectedCounts,
} from "./affected-count";

const USER_MANAGE = "user.manage";
const subscribeNever = () => () => {};

interface StudentHit {
  id: string;
  display_name: string | null;
  admission_no: string | null;
  class_section: string | null;
}

/** A student as staff know them in the office: name, admission number, class. */
function studentLabel(item: StudentHit): string {
  return [item.display_name, item.admission_no, item.class_section].filter(Boolean).join(" · ");
}

interface Picked {
  id: string;
  label: string;
}

/** Sections grouped by class, in class order (as `useSectionOptions` sorts them). */
function byClass(sections: readonly SectionOption[]) {
  const groups = new Map<string, { classId: string; label: string; sections: SectionOption[] }>();
  for (const section of sections) {
    const group = groups.get(section.classId) ?? {
      classId: section.classId,
      label: section.classLabel || section.label,
      sections: [],
    };
    group.sections.push(section);
    groups.set(section.classId, group);
  }
  return [...groups.values()];
}

function toggle(list: readonly string[], id: string, on: boolean): string[] {
  return on ? [...new Set([...list, id])] : list.filter((item) => item !== id);
}

/**
 * The affected-people picker (D59, ADR-0050): pick students by section or one by one, and staff
 * by role or one by one; the API counts students, their guardians and staff, each person once,
 * and "Use these counts" puts the numbers into the edit form. The selection lives only in this
 * dialog: it is never sent to be stored (D41), only the counts the school then saves.
 *
 * Rendered in a portal so its controls are not part of the edit form behind it (Enter in the
 * search box must not save the breach).
 */
export function AffectedPicker({
  breachId,
  onUse,
}: {
  breachId: string;
  onUse: (counts: AffectedCounts) => void;
}) {
  const t = useTranslations("breaches.picker");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const can = useStaffCan();
  const manageUsers = can(USER_MANAGE);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  /** Counts chosen with "Use these counts", handed over when the picker has closed. */
  const usedCounts = useRef<AffectedCounts | null>(null);
  const titleId = useId();
  const descriptionId = useId();
  const [open, setOpen] = useState(false);
  // The portal target exists only in the browser (no portal in the server render).
  const mounted = useSyncExternalStore(
    subscribeNever,
    () => true,
    () => false,
  );
  const close = useDialogClose(dialogRef, "modal");

  const [allStudents, setAllStudents] = useState(false);
  const [sectionIds, setSectionIds] = useState<string[]>([]);
  const [students, setStudents] = useState<Picked[]>([]);
  const [allStaff, setAllStaff] = useState(false);
  const [roleKeys, setRoleKeys] = useState<string[]>([]);
  const [staff, setStaff] = useState<Picked[]>([]);
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState<string | null>(null);
  const [memberChoice, setMemberChoice] = useState("");

  const sections = useSectionOptions();
  const groups = useMemo(() => byClass(sections.sections), [sections.sections]);
  const roles = useSchoolRoles(open && manageUsers);
  const members = useSchoolMembers(open && manageUsers);
  const results = useQuery({
    queryKey: ["staff", "breaches", "picker-search", term],
    queryFn: async () =>
      (
        await unwrap(
          api.GET("/api/v1/students", { params: { query: { query: term as string, limit: 20 } } }),
        )
      ).data,
    enabled: open && term !== null && term.length >= 2,
    retry: false,
  });

  const request: AffectedCountRequest = {
    all_students: allStudents,
    class_ids: [],
    section_ids: allStudents ? [] : sectionIds,
    student_ids: allStudents ? [] : students.map((item) => item.id),
    all_staff: allStaff,
    staff_role_keys: allStaff ? [] : roleKeys,
    membership_ids: allStaff ? [] : staff.map((item) => item.id),
  };
  const selectionKey = JSON.stringify(request);
  const count = useMutation({
    mutationFn: (body: AffectedCountRequest) => countAffected(api, breachId, body),
  });
  const [countedFor, setCountedFor] = useState<string | null>(null);
  const counts = countedFor === selectionKey ? count.data : undefined;
  const empty = isEmptySelection(request);

  function show() {
    setOpen(true);
    dialogRef.current?.showModal();
  }

  function runCount() {
    setCountedFor(selectionKey);
    count.mutate(request);
  }

  function use() {
    if (!counts) return;
    // Filled once the picker has closed (onClose): closing returns focus to the trigger, which
    // would otherwise take it back from the count field `onUse` focuses (D66 journey).
    usedCounts.current = counts;
    close();
  }

  function addStudent(item: StudentHit) {
    if (students.some((s) => s.id === item.id) || students.length >= MAX_PICKED) return;
    setStudents([...students, { id: item.id, label: studentLabel(item) }]);
  }

  function addMember() {
    const member = members.data?.find((item) => item.membership_id === memberChoice);
    if (!member || staff.some((s) => s.id === member.membership_id)) return;
    setStaff([...staff, { id: member.membership_id, label: member.display_name }]);
    setMemberChoice("");
  }

  const fmt = (value: number) => formatCount(value, locale) ?? "0";
  const dialog = (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      // React passes a portal's events to its React parents: without this, closing the picker
      // (or Escape in it) would also close the edit dialog it was opened from.
      onCancel={(event) => event.stopPropagation()}
      onClose={(event) => {
        event.stopPropagation();
        setOpen(false);
        const used = usedCounts.current;
        usedCounts.current = null;
        if (used) onUse(used);
        else triggerRef.current?.focus();
      }}
      className="dialog-motion m-auto max-h-[calc(100dvh-2rem)] w-[min(44rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-0 text-ink shadow-popover"
    >
      <div className="flex items-start justify-between gap-4 p-4 pb-2 sm:p-6 sm:pb-2">
        <div className="min-w-0 space-y-1">
          <h2 id={titleId} className="text-lg font-semibold">
            {t("title")}
          </h2>
          <p id={descriptionId} className="text-sm text-ink-muted">
            {t("description")}
          </p>
        </div>
        <DialogCloseButton label={tc("close")} onClick={close} />
      </div>
      {open ? (
        <div className="space-y-6 px-4 py-4 sm:px-6">
          <fieldset className="space-y-3">
            <legend className="text-base font-semibold">{t("studentsTitle")}</legend>
            <label className="flex min-h-10 cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={allStudents}
                onChange={(event) => setAllStudents(event.target.checked)}
              />
              <span>{t("allStudents")}</span>
            </label>
            {!allStudents ? (
              <>
                <p className="text-sm text-ink-muted">{t("sectionsHint")}</p>
                {sections.loading ? (
                  <p className="text-sm">{tc("loading")}</p>
                ) : groups.length === 0 ? (
                  <p className="text-sm">{t("noSections")}</p>
                ) : (
                  <div className="grid gap-3 sm:grid-cols-2">
                    {groups.map((group) => (
                      <fieldset key={group.classId} className="space-y-1">
                        <legend className="text-sm font-semibold">{group.label}</legend>
                        {group.sections.map((section) => (
                          <label
                            key={section.id}
                            className="flex min-h-10 cursor-pointer items-center gap-2 rounded-md px-1 text-sm hover:bg-surface-muted pointer-coarse:min-h-11"
                          >
                            <input
                              type="checkbox"
                              className="size-4 accent-primary"
                              checked={sectionIds.includes(section.id)}
                              onChange={(event) =>
                                setSectionIds(toggle(sectionIds, section.id, event.target.checked))
                              }
                            />
                            <span>{section.label}</span>
                          </label>
                        ))}
                      </fieldset>
                    ))}
                  </div>
                )}
                <div className="space-y-2">
                  <div className="flex flex-wrap items-end gap-2">
                    <SearchInput
                      label={t("studentSearch")}
                      labelVisible
                      value={search}
                      wrapperClassName="min-w-0 flex-1"
                      autoComplete="off"
                      onChange={(event) => setSearch(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          setTerm(search.trim());
                        }
                      }}
                    />
                    <Button variant="secondary" onClick={() => setTerm(search.trim())}>
                      {t("find")}
                    </Button>
                  </div>
                  <div role="status" aria-live="polite" className="text-sm">
                    {term !== null && term.length < 2
                      ? t("searchTooShort")
                      : results.isFetching
                        ? tc("loading")
                        : results.data
                          ? t("resultsCount", { count: results.data.length })
                          : null}
                  </div>
                  {results.error ? (
                    <ApiErrorAlert error={results.error} namespace="breaches" />
                  ) : null}
                  {results.data && results.data.length > 0 ? (
                    <ul
                      aria-label={t("results")}
                      className="divide-y divide-border rounded-md border border-border"
                    >
                      {results.data.map((item) => {
                        const added = students.some((s) => s.id === item.id);
                        return (
                          <li
                            key={item.id}
                            className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm"
                          >
                            <span>{studentLabel(item)}</span>
                            <Button
                              size="sm"
                              variant="secondary"
                              disabled={added}
                              aria-label={t("addStudent", { name: studentLabel(item) })}
                              onClick={() => addStudent(item)}
                            >
                              {added ? t("added") : t("add")}
                            </Button>
                          </li>
                        );
                      })}
                    </ul>
                  ) : null}
                  {students.length > 0 ? (
                    <PickedList
                      label={t("pickedStudents", { count: students.length })}
                      items={students}
                      removeLabel={(name) => t("remove", { name })}
                      onRemove={(id) => setStudents(students.filter((s) => s.id !== id))}
                    />
                  ) : null}
                </div>
              </>
            ) : null}
          </fieldset>

          <fieldset className="space-y-3">
            <legend className="text-base font-semibold">{t("staffTitle")}</legend>
            <label className="flex min-h-10 cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={allStaff}
                onChange={(event) => setAllStaff(event.target.checked)}
              />
              <span>{t("allStaff")}</span>
            </label>
            {!allStaff && !manageUsers ? (
              <p className="text-sm text-ink-muted">{t("staffNeedsUsers")}</p>
            ) : null}
            {!allStaff && manageUsers ? (
              <>
                {roles.data && roles.data.length > 0 ? (
                  <fieldset className="space-y-1">
                    <legend className="text-sm font-semibold">{t("byRole")}</legend>
                    <div className="grid gap-1 sm:grid-cols-2">
                      {roles.data.map((role) => (
                        <label
                          key={role.key}
                          className="flex min-h-10 cursor-pointer items-center gap-2 rounded-md px-1 text-sm hover:bg-surface-muted pointer-coarse:min-h-11"
                        >
                          <input
                            type="checkbox"
                            className="size-4 accent-primary"
                            checked={roleKeys.includes(role.key)}
                            onChange={(event) =>
                              setRoleKeys(toggle(roleKeys, role.key, event.target.checked))
                            }
                          />
                          <span>{role.name_en}</span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ) : null}
                {members.data && members.data.length > 0 ? (
                  <div className="flex flex-wrap items-end gap-2">
                    <label className="min-w-0 flex-1 space-y-1 text-sm">
                      <span className="font-semibold">{t("oneStaff")}</span>
                      <select
                        className="block min-h-10 w-full rounded-md border border-border bg-surface px-3"
                        value={memberChoice}
                        onChange={(event) => setMemberChoice(event.target.value)}
                      >
                        <option value="">{tc("chooseOne")}</option>
                        {members.data.map((member) => (
                          <option key={member.membership_id} value={member.membership_id}>
                            {member.display_name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <Button variant="secondary" disabled={!memberChoice} onClick={addMember}>
                      {t("add")}
                    </Button>
                  </div>
                ) : null}
                {staff.length > 0 ? (
                  <PickedList
                    label={t("pickedStaff", { count: staff.length })}
                    items={staff}
                    removeLabel={(name) => t("remove", { name })}
                    onRemove={(id) => setStaff(staff.filter((s) => s.id !== id))}
                  />
                ) : null}
              </>
            ) : null}
          </fieldset>

          <div className="space-y-2 rounded-md border border-border bg-surface-muted p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm text-ink-muted">{t("countHint")}</p>
              <Button variant="secondary" disabled={empty || count.isPending} onClick={runCount}>
                {t("count")}
              </Button>
            </div>
            <div role="status" aria-live="polite">
              {count.isPending ? (
                <p className="text-sm">{tc("loading")}</p>
              ) : counts ? (
                <p className="text-sm font-semibold">
                  {t("result", {
                    students: fmt(counts.students),
                    guardians: fmt(counts.guardians),
                    staff: fmt(counts.staff),
                  })}
                </p>
              ) : countedFor !== null && countedFor !== selectionKey ? (
                <p className="text-sm">{t("countAgain")}</p>
              ) : null}
            </div>
            {count.error && countedFor === selectionKey ? (
              <ApiErrorAlert error={count.error} namespace="breaches" />
            ) : null}
          </div>
        </div>
      ) : null}
      <div className="dialog-footer flex flex-wrap justify-end gap-2 rounded-b-xl border-t border-border bg-surface-muted px-4 py-3 max-sm:[&>*]:flex-1 sm:px-6 sm:py-4">
        <Button variant="secondary" onClick={close}>
          {tc("cancel")}
        </Button>
        <Button disabled={!counts} onClick={use}>
          {t("use")}
        </Button>
      </div>
    </dialog>
  );

  return (
    <>
      <Button ref={triggerRef} variant="secondary" size="sm" aria-haspopup="dialog" onClick={show}>
        {t("open")}
      </Button>
      {mounted ? createPortal(dialog, document.body) : null}
    </>
  );
}

function PickedList({
  label,
  items,
  removeLabel,
  onRemove,
}: {
  label: string;
  items: readonly Picked[];
  removeLabel: (name: string) => string;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="space-y-1">
      <p className="text-sm font-semibold">{label}</p>
      <ul className="flex flex-wrap gap-2">
        {items.map((item) => (
          <li
            key={item.id}
            className="flex items-center gap-1 rounded-full border border-border px-3 py-1 text-sm"
          >
            <span>{item.label}</span>
            <Button
              size="sm"
              variant="ghost"
              aria-label={removeLabel(item.label)}
              onClick={() => onRemove(item.id)}
            >
              ×
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
