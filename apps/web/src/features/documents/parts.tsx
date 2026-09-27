"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useMemo, useState } from "react";
import { z } from "zod";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge } from "@/components/ui/Badge";
import { Button, type ButtonVariant } from "@/components/ui/Button";
import { useSectionOptions } from "@/features/findings/data";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { translateOr } from "@/lib/i18n-dynamic";
import { DOCUMENT_KEYS, startDocumentDownload, useSchoolMembers, useSchoolRoles } from "./data";
import {
  badgeTone,
  DOCUMENT_PERM,
  MAX_ACL,
  PRINCIPAL_TYPES,
  versionBadge,
  versionReason,
  type AclEntry,
  type DocumentVersion,
  type PrincipalType,
} from "./types";

export function VersionStatusBadge({
  version,
}: {
  version: Pick<DocumentVersion, "status" | "error">;
}) {
  const t = useTranslations("documents.version.status");
  const badge = versionBadge(version);
  return <Badge tone={badgeTone[badge]}>{t(badge)}</Badge>;
}

/**
 * Plain-language reason a version cannot be opened (virus found, withheld because it showed an
 * Aadhaar number, check not finished …). Only codes reach the screen: never the file's text,
 * and never the number itself.
 */
export function VersionReason({ version }: { version: Pick<DocumentVersion, "status" | "error"> }) {
  const t = useTranslations("documents.version.reason");
  const reason = versionReason(version);
  if (reason === null) return null;
  return <>{translateOr(t, reason, "quarantined_other")}</>;
}

// --- who can see it ------------------------------------------------------------------------

/** System role keys (apps/api/app/authz/roles.yaml), named from `school.users.roles`. */
export const SYSTEM_ROLES = [
  "owner",
  "principal",
  "office_admin",
  "office_staff",
  "accountant",
  "exam_coordinator",
  "class_teacher",
  "teacher",
  "auditor_readonly",
] as const;

export interface AclOption {
  type: PrincipalType;
  ref: string;
  label: string;
}

/** `role:office_admin` ⇄ ACL entry (the checkbox value). */
export function aclValue(entry: Pick<AclEntry, "principal_type" | "principal_ref">): string {
  return `${entry.principal_type}:${entry.principal_ref}`;
}

export function parseAclValue(value: string): AclEntry | null {
  const colon = value.indexOf(":");
  if (colon < 1) return null;
  const type = value.slice(0, colon);
  const ref = value.slice(colon + 1);
  if (!(PRINCIPAL_TYPES as readonly string[]).includes(type) || !ref || ref.length > 64) {
    return null;
  }
  return { principal_type: type as PrincipalType, principal_ref: ref };
}

/** zod field for the ACL checkboxes (values `type:ref`), with the API's limit. */
export const aclField = z
  .array(z.string().refine((value) => parseAclValue(value) !== null, { error: "chooseOption" }))
  .max(MAX_ACL, { error: "tooManyAclEntries" });

/** "Whole school" or "only some": the second needs at least one choice. */
export const visibilityField = z.enum(["school", "limited"], { error: "chooseOption" });

export function refineAcl(
  data: { visibility: "school" | "limited"; acl: string[] },
  context: z.RefinementCtx,
): void {
  if (data.visibility === "limited" && data.acl.length === 0) {
    context.addIssue({ code: "custom", path: ["acl"], message: "chooseWhoCanSee" });
  }
}

export function aclBody(data: { visibility: "school" | "limited"; acl: string[] }): AclEntry[] {
  if (data.visibility === "school") return [];
  return data.acl.map(parseAclValue).filter((entry): entry is AclEntry => entry !== null);
}

/** Checked ACL boxes of a form. */
export function aclExtra(form: HTMLFormElement): { acl: string[] } {
  return {
    acl: new FormData(form)
      .getAll("acl")
      .filter((value): value is string => typeof value === "string"),
  };
}

/** Server field (`acl.3.principal_ref`) → form field. */
export function documentFieldMap(field: string): string | undefined {
  const [head] = field.split(".");
  return head;
}

/**
 * Names for every principal a member may choose: roles (the school's own names with
 * user.manage, else the system roles), classes and sections of the current year (scoped staff
 * get only theirs), and staff members (with user.manage).
 */
export function useAclOptions(): { options: AclOption[]; loading: boolean } {
  const t = useTranslations("school.users.roles");
  const td = useTranslations("documents.acl");
  const locale = useLocale();
  const can = useStaffCan();
  const manageUsers = can(DOCUMENT_PERM.userManage);
  const { sections, loading } = useSectionOptions();
  const roles = useSchoolRoles(manageUsers);
  const members = useSchoolMembers(manageUsers);
  const options = useMemo(() => {
    const out: AclOption[] = [];
    if (roles.data && roles.data.length > 0) {
      for (const role of roles.data) {
        const name = locale === "te" && role.name_te ? role.name_te : role.name_en;
        out.push({ type: "role", ref: role.key, label: name || role.key });
      }
    } else {
      for (const key of SYSTEM_ROLES) out.push({ type: "role", ref: key, label: t(key) });
    }
    const classes = new Map<string, string>();
    for (const section of sections) {
      if (!classes.has(section.classId)) {
        classes.set(section.classId, section.classLabel || section.label);
      }
    }
    for (const [id, label] of classes) out.push({ type: "class", ref: id, label });
    for (const section of sections)
      out.push({ type: "section", ref: section.id, label: section.label });
    for (const member of members.data ?? []) {
      if (member.status === "removed") continue;
      out.push({
        type: "membership",
        ref: member.membership_id,
        label: member.display_name ?? td("unnamedMember"),
      });
    }
    return out;
  }, [roles.data, members.data, sections, locale, t, td]);
  return { options, loading };
}

/** Label of one ACL entry; unknown references are named by their kind only (never a raw id). */
export function useAclLabel(): (entry: AclEntry) => string {
  const { options } = useAclOptions();
  const t = useTranslations("documents.acl");
  return (entry) => {
    const found = options.find(
      (option) => option.type === entry.principal_type && option.ref === entry.principal_ref,
    );
    if (found) return found.label;
    return t(`unknown.${entry.principal_type}`);
  };
}

/** "Whole school" or the list of who can see it. */
export function AclSummary({ acl }: { acl: readonly AclEntry[] }) {
  const t = useTranslations("documents.acl");
  const label = useAclLabel();
  if (acl.length === 0) return <p>{t("wholeSchool")}</p>;
  return (
    <div className="space-y-2">
      <p>{t("limitedTo")}</p>
      <ul className="list-disc space-y-1 pl-5">
        {acl.map((entry) => (
          <li key={aclValue(entry)}>
            <span className="text-ink-muted">{t(`kind.${entry.principal_type}`)}: </span>
            <span className="font-semibold">{label(entry)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Who can see the document: the whole school (everyone with document access), or only some
 * roles, classes, sections or staff members. Checkboxes are grouped per kind in fieldsets;
 * entries already on the document stay listed even when their name is unknown here.
 */
export function AclFields({
  errors,
  initial = [],
}: {
  errors: Record<string, string>;
  initial?: readonly AclEntry[];
}) {
  const t = useTranslations("documents.acl");
  const tc = useTranslations("common");
  const { options, loading } = useAclOptions();
  const [mode, setMode] = useState<"school" | "limited">(initial.length > 0 ? "limited" : "school");
  const errorId = useId();
  const error = errors.acl ?? errors.visibility;
  const chosen = new Set(initial.map(aclValue));
  const all = [...options];
  for (const entry of initial) {
    if (
      !all.some(
        (option) =>
          aclValue({ principal_type: option.type, principal_ref: option.ref }) === aclValue(entry),
      )
    ) {
      all.push({
        type: entry.principal_type,
        ref: entry.principal_ref,
        label: t(`unknown.${entry.principal_type}`),
      });
    }
  }
  const box = "inline-flex min-h-8 items-center gap-2 text-sm";
  let first = true;
  return (
    <fieldset className="space-y-3" aria-describedby={error ? errorId : undefined}>
      <legend className="text-sm font-semibold text-ink">{t("legend")}</legend>
      <p className="text-sm text-ink-muted">{t("hint")}</p>
      <div className="flex flex-wrap gap-x-6 gap-y-1">
        {(["school", "limited"] as const).map((value) => (
          <label key={value} className={box}>
            <input
              type="radio"
              name="visibility"
              value={value}
              checked={mode === value}
              onChange={() => setMode(value)}
              className="size-4"
            />
            {t(`mode.${value}`)}
          </label>
        ))}
      </div>
      {mode === "limited" && loading ? <p className="text-sm">{tc("loading")}</p> : null}
      {mode === "limited"
        ? PRINCIPAL_TYPES.map((type) => {
            const group = all.filter((option) => option.type === type);
            if (group.length === 0) return null;
            return (
              <fieldset key={type} className="space-y-1">
                <legend className="text-sm font-semibold text-ink-muted">
                  {t(`group.${type}`)}
                </legend>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {group.map((option) => {
                    const value = aclValue({
                      principal_type: option.type,
                      principal_ref: option.ref,
                    });
                    const invalid = first && Boolean(error);
                    first = false;
                    return (
                      <label key={value} className={box}>
                        <input
                          type="checkbox"
                          name="acl"
                          value={value}
                          defaultChecked={chosen.has(value)}
                          aria-invalid={invalid || undefined}
                          className="size-4"
                        />
                        {option.label}
                      </label>
                    );
                  })}
                </div>
              </fieldset>
            );
          })
        : null}
      {error ? (
        <p id={errorId} className="text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

// --- download -------------------------------------------------------------------------------

/** Codes whose answer means the document itself changed: reload it. */
const STALE = new Set(["document_not_ready"]);

/**
 * Download button. The presigned link (≤ 5 minutes, saved as a file) is fetched through the
 * BFF when pressed and opened at once; it is never stored or logged. Only versions that passed
 * the virus check are served.
 */
export function DownloadButton({
  documentId,
  versionNo,
  label,
  description,
  variant = "primary",
}: {
  documentId: string;
  versionNo?: number;
  label: string;
  description?: string;
  variant?: ButtonVariant;
}) {
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const hintId = useId();

  async function download() {
    setPending(true);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/documents/{document_id}/download-url", {
          params: {
            path: { document_id: documentId },
            ...(versionNo !== undefined ? { query: { version: versionNo } } : {}),
          },
        }),
      );
      startDocumentDownload(link.url);
    } catch (failure) {
      setError(failure);
      if (failure instanceof ApiError && failure.code && STALE.has(failure.code)) {
        void queryClient.invalidateQueries({ queryKey: DOCUMENT_KEYS.all });
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-2">
      <Button
        variant={variant}
        size={variant === "primary" ? "md" : "sm"}
        onClick={() => void download()}
        disabled={pending}
        aria-disabled={pending || undefined}
        aria-describedby={description ? hintId : undefined}
      >
        {pending ? tc("working") : label}
      </Button>
      {description ? (
        <span id={hintId} className="sr-only">
          {description}
        </span>
      ) : null}
      <ApiErrorAlert error={error} namespace="documents" />
    </div>
  );
}
