"use client";

import { TENANT_ROLES, type Me, type TenantRoleKey } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import { z } from "zod";
import { Badge } from "@/components/ui/Badge";
import { useSectionOptions, type SectionOption } from "@/features/findings/data";
import { memberTone } from "@/features/status";
import type { Locale } from "@/i18n/routing";
import { formatList } from "@/lib/format";
import { uuid } from "@/lib/validation";
import { roleName } from "./data";
import {
  ASSIGN_ANY_ROLES,
  MAX_SCOPES,
  MFA_ROLES,
  USER_PERM,
  type MemberStatus,
  type ScopeInput,
  type StaffRole,
  type StaffScope,
} from "./types";

const SYSTEM_ROLES: readonly string[] = TENANT_ROLES;

function isSystemRole(role: string): role is TenantRoleKey {
  return SYSTEM_ROLES.includes(role);
}

export function UserStatusBadge({ status }: { status: MemberStatus }) {
  const t = useTranslations("status.member");
  return <Badge tone={memberTone[status]}>{t(status)}</Badge>;
}

/**
 * Role key → name in the reader's language: the school's own name from GET /roles, else the
 * built-in name from the messages, else the key itself (e.g. a role made for this school
 * while /roles could not be read).
 */
export function useRoleLabel(roles: readonly StaffRole[] | undefined): (key: string) => string {
  const t = useTranslations("school.users");
  const locale = useLocale();
  return (key: string) =>
    roleName(roles, key, locale) ?? (isSystemRole(key) ? t(`roles.${key}`) : key);
}

/** "Whole school", "1 class and 2 sections" or "None chosen". */
export function useScopeSummary(): (scopes: readonly StaffScope[]) => string {
  const t = useTranslations("school.users");
  const locale = useLocale() as Locale;
  return (scopes) => {
    if (scopes.some((scope) => scope.type === "school")) return t("scopeSchool");
    const classes = scopes.filter((scope) => scope.type === "class").length;
    const sections = scopes.filter((scope) => scope.type === "section").length;
    if (classes === 0 && sections === 0) return t("scopeNone");
    const parts = [
      ...(classes > 0 ? [t("scopeClasses", { count: classes })] : []),
      ...(sections > 0 ? [t("scopeSections", { count: sections })] : []),
    ];
    return formatList(parts, locale);
  };
}

// --- roles ------------------------------------------------------------------------------------

type Member = Pick<Me, "roles" | "permissions">;

function holdsAll(role: StaffRole, member: Member): boolean {
  return role.permissions.every((permission) => member.permissions.includes(permission));
}

/**
 * Whether the signed-in member may give or take away `role` (mirrors the API's
 * `_guard_grantable` / `_guard_invite_roles`). UX only: roles the API would refuse with 403
 * `role_not_grantable` are shown greyed out with the reason; the API checks every change.
 *
 * - `assign` (PUT /users/{id}/roles): the owner may change any role; everyone else only roles
 *   whose permissions they hold themselves.
 * - `invite` (POST /users): any built-in role without two-step sign-in may be given with
 *   `user.manage`; the other roles need `role.assign` and the same rule as `assign`.
 */
export function canGrantRole(role: StaffRole, member: Member, mode: "invite" | "assign"): boolean {
  if (member.roles.some((key) => ASSIGN_ANY_ROLES.includes(key))) return true;
  if (mode === "invite" && role.is_system && !MFA_ROLES.includes(role.key)) return true;
  if (mode === "invite" && !member.permissions.includes(USER_PERM.assign)) return false;
  return holdsAll(role, member);
}

export const rolesField = z
  .array(z.string().regex(/^[a-z][a-z0-9_]{1,63}$/, { error: "chooseRole" }))
  .min(1, { error: "chooseRole" })
  .max(20, { error: "tooManyRoles" });

/**
 * Role checkboxes (name="roles"). Roles the member cannot change are disabled; when such a
 * role is already held, a hidden input keeps it in the form so saving never drops it.
 */
export function RoleCheckboxes({
  roles,
  selected,
  grantable,
  error,
  legend,
}: {
  roles: readonly StaffRole[];
  selected: readonly string[];
  grantable: (role: StaffRole) => boolean;
  error?: string | undefined;
  legend: string;
}) {
  const t = useTranslations("school.users.rolesForm");
  const label = useRoleLabel(roles);
  const errorId = useId();
  const baseId = useId();
  const listed = new Set(roles.map((role) => role.key));
  const unlisted = selected.filter((key) => !listed.has(key));
  const firstEnabled = roles.find((role) => grantable(role))?.key;
  return (
    <fieldset className="space-y-2" aria-describedby={error ? errorId : undefined}>
      <legend className="text-sm font-semibold text-ink">{legend}</legend>
      <p className="text-sm text-ink-muted">{t("hint")}</p>
      <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {roles.map((role) => {
          const checked = selected.includes(role.key);
          const allowed = grantable(role);
          const hintId = `${baseId}-${role.key}-hint`;
          const invalid = role.key === firstEnabled && Boolean(error);
          const notes = [
            ...(MFA_ROLES.includes(role.key) ? [t("needsMfa")] : []),
            ...(!role.is_system ? [t("schoolRole")] : []),
            ...(!allowed ? [t("notGrantable")] : []),
          ];
          return (
            <div key={role.key} className="flex items-start gap-2 text-sm">
              <input
                id={`${baseId}-${role.key}`}
                type="checkbox"
                name="roles"
                value={role.key}
                defaultChecked={checked}
                disabled={!allowed}
                aria-invalid={invalid || undefined}
                aria-describedby={notes.length > 0 ? hintId : undefined}
                className="mt-1 size-4 accent-primary"
              />
              {!allowed && checked ? <input type="hidden" name="roles" value={role.key} /> : null}
              <div>
                <label htmlFor={`${baseId}-${role.key}`} className="font-semibold">
                  {label(role.key)}
                </label>
                {notes.length > 0 ? (
                  <p id={hintId} className="text-xs text-ink-muted">
                    {notes.join(" ")}
                  </p>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
      {unlisted.map((key) => (
        <input key={key} type="hidden" name="roles" value={key} />
      ))}
      {error ? (
        <p id={errorId} className="text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

// --- classes and sections -----------------------------------------------------------------------

export const SCOPE_MODES = ["none", "chosen", "school"] as const;
export type ScopeMode = (typeof SCOPE_MODES)[number];

/** zod fields of the scope editor (API: `school`, or class and section ids, or nothing). */
export const scopeFields = {
  scope_mode: z.enum(SCOPE_MODES, { error: "chooseOption" }),
  class_refs: z.array(uuid),
  section_refs: z.array(uuid),
};

export interface ScopeValues {
  scope_mode: ScopeMode;
  class_refs: string[];
  section_refs: string[];
}

export function scopeExtra(form: HTMLFormElement | FormData) {
  const data = form instanceof FormData ? form : new FormData(form);
  const list = (name: string) =>
    data.getAll(name).filter((value): value is string => typeof value === "string");
  return { class_refs: list("class_refs"), section_refs: list("section_refs") };
}

/** "Choose at least one" for the chosen mode; the API takes at most 200 scopes. */
export function refineScopes(data: ScopeValues, context: z.RefinementCtx): void {
  if (data.scope_mode !== "chosen") return;
  if (data.class_refs.length === 0 && data.section_refs.length === 0) {
    context.addIssue({ code: "custom", path: ["class_refs"], message: "chooseScope" });
  }
  if (data.class_refs.length + data.section_refs.length > MAX_SCOPES) {
    context.addIssue({ code: "custom", path: ["class_refs"], message: "tooManyScopes" });
  }
}

/** Scope list for the API (ScopeIn[]). */
export function scopesBody(data: ScopeValues): ScopeInput[] {
  if (data.scope_mode === "school") return [{ type: "school", ref: null }];
  if (data.scope_mode === "none") return [];
  return [
    ...data.class_refs.map((ref) => ({ type: "class" as const, ref })),
    ...data.section_refs.map((ref) => ({ type: "section" as const, ref })),
  ];
}

export function scopeModeOf(scopes: readonly StaffScope[]): ScopeMode {
  if (scopes.some((scope) => scope.type === "school")) return "school";
  return scopes.length > 0 ? "chosen" : "none";
}

interface ClassOption {
  id: string;
  label: string;
}

function classesOf(sections: readonly SectionOption[]): ClassOption[] {
  const seen = new Map<string, ClassOption>();
  for (const section of sections) {
    if (!seen.has(section.classId)) {
      seen.set(section.classId, {
        id: section.classId,
        label: section.classLabel || section.label,
      });
    }
  }
  return [...seen.values()];
}

/**
 * Which classes and sections the person's class-based roles reach (FR-IAM-012): the whole
 * school, some classes or sections of the current academic year, or none. Class and section
 * choices already held but not in this year's list stay ticked (and can be unticked) so saving
 * never drops them silently.
 */
export function ScopeEditor({
  initial,
  initialMode,
  errors,
}: {
  initial: readonly StaffScope[];
  initialMode?: ScopeMode;
  errors: Record<string, string>;
}) {
  const t = useTranslations("school.users.scopeForm");
  const tc = useTranslations("common");
  const { sections, loading } = useSectionOptions();
  const [mode, setMode] = useState<ScopeMode>(initialMode ?? scopeModeOf(initial));
  const errorId = useId();
  const error = errors.scope_mode ?? errors.class_refs ?? errors.section_refs;
  const classes = classesOf(sections);
  const heldClasses = new Set(initial.filter((s) => s.type === "class").map((s) => s.ref));
  const heldSections = new Set(initial.filter((s) => s.type === "section").map((s) => s.ref));
  const otherClasses = [...heldClasses].filter(
    (ref): ref is string => !!ref && !classes.some((item) => item.id === ref),
  );
  const otherSections = [...heldSections].filter(
    (ref): ref is string => !!ref && !sections.some((item) => item.id === ref),
  );
  const byClass = new Map<string, SectionOption[]>();
  for (const section of sections) {
    byClass.set(section.classId, [...(byClass.get(section.classId) ?? []), section]);
  }
  const box = "inline-flex min-h-8 items-center gap-2 text-sm";
  // The first box gets aria-invalid so the form can move focus to it.
  const firstBox = classes[0]?.id ?? sections[0]?.id;
  const listError = Boolean(errors.class_refs ?? errors.section_refs);
  const invalidFirst = (id: string) => (listError && id === firstBox) || undefined;
  return (
    <fieldset className="space-y-3" aria-describedby={error ? errorId : undefined}>
      <legend className="text-sm font-semibold text-ink">{t("legend")}</legend>
      <p className="text-sm text-ink-muted">{t("hint")}</p>
      <div className="flex flex-col gap-1">
        {SCOPE_MODES.map((value) => (
          <label key={value} className={box}>
            <input
              type="radio"
              name="scope_mode"
              value={value}
              checked={mode === value}
              onChange={() => setMode(value)}
              className="size-4"
            />
            {t(`mode.${value}`)}
          </label>
        ))}
      </div>
      {mode === "none" ? <p className="text-sm text-ink-muted">{t("noneHint")}</p> : null}
      {mode === "school" ? <p className="text-sm text-ink-muted">{t("schoolHint")}</p> : null}
      {mode === "chosen" ? (
        <div className="space-y-3">
          {loading ? <p className="text-sm">{tc("loading")}</p> : null}
          {!loading && sections.length === 0 ? (
            <p className="text-sm text-ink-muted">{t("noStructure")}</p>
          ) : null}
          {classes.length > 0 || otherClasses.length > 0 ? (
            <fieldset className="space-y-1">
              <legend className="text-sm font-semibold">{t("classesLegend")}</legend>
              <p className="text-xs text-ink-muted">{t("classesHint")}</p>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {classes.map((item) => (
                  <label key={item.id} className={box}>
                    <input
                      type="checkbox"
                      name="class_refs"
                      value={item.id}
                      defaultChecked={heldClasses.has(item.id)}
                      aria-invalid={invalidFirst(item.id)}
                      className="size-4"
                    />
                    {item.label}
                  </label>
                ))}
                {otherClasses.map((ref) => (
                  <label key={ref} className={box}>
                    <input
                      type="checkbox"
                      name="class_refs"
                      value={ref}
                      defaultChecked
                      className="size-4"
                    />
                    {t("otherClass")}
                  </label>
                ))}
              </div>
            </fieldset>
          ) : null}
          {sections.length > 0 || otherSections.length > 0 ? (
            <fieldset className="space-y-2">
              <legend className="text-sm font-semibold">{t("sectionsLegend")}</legend>
              {[...byClass.entries()].map(([classId, list]) => (
                <div key={classId} className="flex flex-wrap gap-x-4 gap-y-1">
                  {list.map((section) => (
                    <label key={section.id} className={box}>
                      <input
                        type="checkbox"
                        name="section_refs"
                        value={section.id}
                        defaultChecked={heldSections.has(section.id)}
                        aria-invalid={invalidFirst(section.id)}
                        className="size-4"
                      />
                      {section.label}
                    </label>
                  ))}
                </div>
              ))}
              {otherSections.length > 0 ? (
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {otherSections.map((ref) => (
                    <label key={ref} className={box}>
                      <input
                        type="checkbox"
                        name="section_refs"
                        value={ref}
                        defaultChecked
                        className="size-4"
                      />
                      {t("otherSection")}
                    </label>
                  ))}
                </div>
              ) : null}
            </fieldset>
          ) : null}
        </div>
      ) : null}
      {error ? (
        <p id={errorId} className="text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

/** Server field (dotted body path) → form field name of the user forms. */
export function userFieldMap(field: string): string | undefined {
  const [head] = field.split(".");
  if (head === "scopes") return "scope_mode";
  return head;
}
