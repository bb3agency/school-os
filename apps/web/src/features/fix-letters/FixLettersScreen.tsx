"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Link } from "@/i18n/navigation";
import { toLoadable, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import * as z from "@/lib/zod";
import {
  FIX_LETTERS_BOARD_PRINT,
  FIX_LETTERS_MANAGE,
  FIX_LETTER_KEYS,
  FIX_LETTER_PROFILES,
  boardLetterHref,
  bySection,
  fixLettersApi,
  parentLettersHref,
  type FixLetter,
  type FixLetterStudent,
} from "./api";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** "Letters given" / "Letter sent": records the letter's follow-up step for these students. */
function RecordSent({
  profile,
  letter,
  students,
  label,
}: {
  profile: string;
  letter: FixLetter;
  students: readonly FixLetterStudent[];
  label: string;
}) {
  const t = useTranslations("fixLetters.sent");
  const api = useBffClient("staff");
  const max = today();
  return (
    <ActionDialog
      triggerLabel={label}
      triggerVariant="secondary"
      triggerSize="sm"
      triggerDisabled={students.length === 0}
      title={t(`${letter.key}.title`)}
      description={t(`${letter.key}.description`, { count: students.length })}
      confirmLabel={t("confirm")}
      consequence={t("consequence")}
      schema={z.object({ occurred_on: z.string().min(1, { error: "required" }) })}
      invalidate={[FIX_LETTER_KEYS.all]}
      errorNamespace="fixLetters"
      errorSummary
      submit={(data, key) =>
        fixLettersApi.recordSent(
          api,
          profile,
          letter.key,
          students.map((s) => s.student.id),
          data.occurred_on,
          key,
        )
      }
    >
      {(errors) => (
        <TextField
          label={t("date")}
          name="occurred_on"
          type="date"
          max={max}
          defaultValue={max}
          error={errors.occurred_on}
          required
        />
      )}
    </ActionDialog>
  );
}

function PrintLink({ href, label }: { href: string; label: string }) {
  const t = useTranslations("fixLetters");
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={buttonClasses("secondary", "sm")}
    >
      <Icon name="file" className="size-4" />
      {label}
      <span className="sr-only"> ({t("newTab")})</span>
    </a>
  );
}

function StudentsTable({
  id,
  rows,
  action,
}: {
  id: string;
  rows: FixLetterStudent[];
  action?: (row: FixLetterStudent) => ReactNode;
}) {
  const t = useTranslations("fixLetters");
  const columns: ColumnV2<FixLetterStudent>[] = [
    {
      key: "student",
      stack: "title",
      header: t("colStudent"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold break-words text-ink">
            {row.student.display_name ?? t("notShown")}
          </span>
          <span className="text-xs text-ink-muted">
            {[row.class_section, row.student.admission_no].filter(Boolean).join(" · ")}
          </span>
        </span>
      ),
    },
    { key: "fields", header: t("colFields"), cell: (row) => row.fields.join(", ") },
    {
      key: "sent",
      header: t("colSent"),
      cell: (row) =>
        row.last_sent_on ? (
          <span className="text-sm">{formatDate(row.last_sent_on) ?? row.last_sent_on}</span>
        ) : (
          <span className="text-sm text-ink-muted">{t("notSent")}</span>
        ),
    },
    ...(action ? [{ key: "action", header: t("colAction"), cell: action }] : []),
  ];
  return (
    <DataTableV2
      id={id}
      stacked
      caption={t("colStudent")}
      captionHidden
      columns={columns}
      state={toLoadable({ data: rows, isPending: false, isError: false, error: null })}
      rowKey={(row) => row.student.id}
      emptyTitle={t("emptyTitle")}
      emptyBody={t("emptyBody")}
    />
  );
}

/** FR-DQ-051..053, FR-DQ-057: letters to parents, per student or per section. */
function ParentLetters({ profile, letter }: { profile: string; letter: FixLetter }) {
  const t = useTranslations("fixLetters");
  return (
    <section aria-labelledby="fix-letters-parent" className="space-y-3">
      <h2 id="fix-letters-parent" className="text-base font-semibold text-ink">
        {letter.label} <span className="font-normal text-ink-muted">({letter.students_count})</span>
      </h2>
      <p className="text-sm text-ink-muted">{t("parentAbout")}</p>
      {letter.students_count === 0 ? (
        <Alert tone="success" title={t("noneTitle")}>
          {t("noneParent")}
        </Alert>
      ) : (
        bySection(letter.students).map((group) => (
          <div key={group.sectionId ?? "none"} className="space-y-2">
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="text-sm font-semibold text-ink">
                {group.label ?? t("noSection")}{" "}
                <span className="font-normal text-ink-muted">({group.students.length})</span>
              </h3>
              {group.sectionId ? (
                <PrintLink
                  href={parentLettersHref(profile, { sectionId: group.sectionId })}
                  label={t("printSection")}
                />
              ) : null}
              <RecordSent
                profile={profile}
                letter={letter}
                students={group.students}
                label={t("sent.parent_aadhaar.trigger")}
              />
            </div>
            <StudentsTable
              id={`fix-letters-parent-${group.sectionId ?? "none"}`}
              rows={group.students}
              action={(row) => (
                <PrintLink
                  href={parentLettersHref(profile, { studentId: row.student.id })}
                  label={t("printOne")}
                />
              )}
            />
          </div>
        ))
      )}
    </section>
  );
}

/** FR-DQ-054, FR-DQ-055, FR-DQ-057: the board letter (export.portal, step-up first). */
function BoardLetter({ profile, letter }: { profile: string; letter: FixLetter }) {
  const t = useTranslations("fixLetters");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const [recipient, setRecipient] = useState(letter.recipients[0]?.key ?? "");
  const [readyFor, setReadyFor] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  async function prepare() {
    setPending(true);
    setError(undefined);
    try {
      await fixLettersApi.checkBoardLetter(api, profile, recipient);
      setReadyFor(recipient);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return (
    <section aria-labelledby="fix-letters-board" className="space-y-3">
      <h2 id="fix-letters-board" className="text-base font-semibold text-ink">
        {letter.label} <span className="font-normal text-ink-muted">({letter.students_count})</span>
      </h2>
      <p className="text-sm text-ink-muted">{t("boardAbout")}</p>
      {letter.students_count === 0 ? (
        <Alert tone="success" title={t("noneTitle")}>
          {t("noneBoard")}
        </Alert>
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-3">
            {can(FIX_LETTERS_BOARD_PRINT) ? (
              <>
                <SelectField
                  label={t("recipient")}
                  name="recipient"
                  value={recipient}
                  onChange={(event) => {
                    setRecipient(event.target.value);
                    setReadyFor(null);
                  }}
                  options={letter.recipients.map((r) => ({ value: r.key, label: r.label }))}
                />
                {readyFor === recipient ? (
                  <a
                    href={boardLetterHref(profile, recipient)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={buttonClasses("primary", "md")}
                  >
                    <Icon name="file" className="size-4" />
                    {t("openBoard")}
                    <span className="sr-only"> ({t("newTab")})</span>
                  </a>
                ) : (
                  <Button onClick={() => void prepare()} disabled={pending} variant="secondary">
                    {pending ? t("checking") : t("prepareBoard")}
                  </Button>
                )}
              </>
            ) : (
              <p className="text-sm text-ink-muted">{t("askPrincipal")}</p>
            )}
            <RecordSent
              profile={profile}
              letter={letter}
              students={letter.students}
              label={t("sent.board_registration.trigger")}
            />
          </div>
          <ApiErrorAlert error={error} namespace="fixLetters" />
          <StudentsTable id="fix-letters-board-table" rows={letter.students} />
        </>
      )}
    </section>
  );
}

/**
 * FA-08 (FR-DQ-049..060): the letters that ask a fix owner outside the school to correct a
 * readiness difference. Templates filled from the school's own records; no AI. The UDISE+
 * letter to the MEO is on the UDISE+ corrections page.
 */
export function FixLettersScreen() {
  const t = useTranslations("fixLetters");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(FIX_LETTERS_MANAGE);
  const [profile, setProfile] = useState<string>(FIX_LETTER_PROFILES[0]);
  const overview = useQuery({
    queryKey: FIX_LETTER_KEYS.overview(profile),
    queryFn: () => fixLettersApi.overview(api, profile),
    enabled: allowed,
    retry: false,
  });
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[
        { label: tn("home"), href: "/" },
        { label: t("readinessCrumb"), href: "/findings/readiness" },
        { label: t("title") },
      ]}
    />
  );
  if (!meLoaded) {
    return (
      <div className="space-y-5">
        {header}
        <LoadingState label={tc("loading")} />
      </div>
    );
  }
  if (!allowed) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }
  const data = overview.data;
  const parent = data?.letters.find((l) => l.key === "parent_aadhaar");
  const board = data?.letters.find((l) => l.key === "board_registration");
  return (
    <div className="space-y-6">
      {header}
      <Alert tone="info" title={t("aboutTitle")}>
        {t("aboutBody")}{" "}
        <Link href="/findings/udise-corrections" className="underline">
          {t("udiseLink")}
        </Link>
      </Alert>
      <SelectField
        label={t("profile")}
        name="profile"
        value={profile}
        onChange={(event) => setProfile(event.target.value)}
        options={FIX_LETTER_PROFILES.map((key) => ({ value: key, label: t(`profiles.${key}`) }))}
      />
      {data && !data.verified ? (
        <Alert tone="warning" title={t("unverifiedTitle")}>
          {t("unverifiedBody")}
        </Alert>
      ) : null}
      <ApiErrorAlert error={overview.error} namespace="fixLetters" />
      {overview.isPending ? <LoadingState label={tc("loading")} /> : null}
      {parent ? <ParentLetters profile={profile} letter={parent} /> : null}
      {board ? <BoardLetter profile={profile} letter={board} /> : null}
    </div>
  );
}
