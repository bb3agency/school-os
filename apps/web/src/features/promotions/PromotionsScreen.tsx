"use client";

import type { AcademicYear, SchoolClass, Section } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge, Pill, type BadgeTone } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { ApiError, useApiMutation, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { ready, type Loadable } from "@/lib/loadable";
import { classLabel, useStructureLists, useYearSections } from "@/features/academic-structure/data";
import {
  AFTER_PROMOTION_KEYS,
  PROMOTE,
  STUDENT_READ,
  commitPromotion,
  committedRun,
  mapKey,
  planRequest,
  previewPromotion,
  sameRequest,
  searchStudents,
  studentLabel,
  targetYears,
  undoPromotion,
  usePromotionRuns,
  useSectionStudents,
  useSourceYear,
  type PromotionOutcome,
  type PromotionPlan,
  type PromotionPreview,
  type PromotionRequest,
  type PromotionRun,
  type PromotionStudent,
  type StudentSummary,
} from "./data";

const STRUCTURE_HREF = "/settings/structure";

const OUTCOME_TONE: Record<PromotionOutcome, BadgeTone> = {
  promoted: "success",
  held_back: "warning",
  graduated: "info",
  skipped: "neutral",
};

/**
 * Year-end promotion of one academic year (FR-TEN-011, US-202 AC2), for holders of
 * `tenant.structure.manage`: choose the next year, mark students who stay in their class,
 * choose sections where the new year has no section of the same name, preview (nothing is
 * changed), then promote in one step (Idempotency-Key, plan fingerprint). A promotion can be
 * undone within 24 hours. The URL holds only the year's ID.
 */
export function PromotionsScreen({ yearId }: { yearId: string }) {
  const t = useTranslations("academicStructure.promotions");
  const me = useStaffMe();
  const can = useStaffCan();
  const manage = can(PROMOTE);
  const year = useSourceYear(yearId, manage);
  const runs = usePromotionRuns(yearId, manage);
  const lists = useStructureLists(false);
  const tn = useTranslations("school.nav");
  // Where the plan is: nothing previewed, a preview that matches the plan, or a stale one.
  const [planStage, setPlanStage] = useState<PlanStage>("plan");
  const crumbs = (label: string) => [
    { label: tn("home"), href: "/" },
    { label: tn("structure"), href: STRUCTURE_HREF },
    { label },
  ];

  const back = (
    <ButtonLink href={STRUCTURE_HREF} variant="secondary">
      {t("back")}
    </ButtonLink>
  );
  if (me === undefined) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("titlePlain")} breadcrumb={crumbs(t("titlePlain"))} actions={back} />
        <LoadingState label={t("loading")} />
      </div>
    );
  }
  if (!manage) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("titlePlain")} breadcrumb={crumbs(t("titlePlain"))} actions={back} />
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      </div>
    );
  }
  if (year.status !== "ready") {
    return (
      <div className="space-y-6">
        <PageHeader title={t("titlePlain")} breadcrumb={crumbs(t("titlePlain"))} actions={back} />
        <LoadableNote state={year} />
      </div>
    );
  }
  const from = year.data;
  const committed = runs.status === "ready" ? committedRun(runs.data) : undefined;
  const step: StepId | "finished" = committed
    ? committed.can_undo
      ? "undo"
      : "finished"
    : planStage === "previewed"
      ? "commit"
      : planStage === "stale"
        ? "preview"
        : "plan";
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title", { label: from.label })}
        description={t("description")}
        breadcrumb={crumbs(t("title", { label: from.label }))}
        actions={back}
      />
      {runs.status === "ready" ? <Steps current={step} /> : null}
      {from.archived_at ? <Alert tone="warning">{t("archivedYear")}</Alert> : null}
      <StatusCard from={from} runs={runs} years={lists.years} />
      {runs.status === "ready" && committed === undefined ? (
        <PlanCard from={from} years={lists.years} classes={lists.classes} onStage={setPlanStage} />
      ) : null}
    </div>
  );
}

type PlanStage = "plan" | "previewed" | "stale";
const STEPS = ["plan", "preview", "commit", "undo"] as const;
type StepId = (typeof STEPS)[number];

/**
 * Where the promotion is: plan → preview → promote → undo within 24 hours. An ordered list;
 * the current step has aria-current="step" and finished steps say "Done" to screen readers
 * (the check mark is decorative). "finished" marks every step done (undo time is over).
 */
function Steps({ current }: { current: StepId | "finished" }) {
  const t = useTranslations("academicStructure.promotions.steps");
  const index = current === "finished" ? STEPS.length : STEPS.indexOf(current);
  return (
    <nav
      aria-label={t("label")}
      className="rounded-xl border border-border bg-surface px-5 py-4 shadow-card"
    >
      <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((step, position) => {
          const done = position < index;
          const now = position === index;
          return (
            <li
              key={step}
              aria-current={now ? "step" : undefined}
              className="flex items-center gap-3"
            >
              <span
                aria-hidden="true"
                className={cn(
                  "flex size-8 shrink-0 items-center justify-center rounded-full border-2 text-sm font-semibold",
                  done
                    ? "border-success bg-success text-white"
                    : now
                      ? "border-action bg-action text-on-action"
                      : "border-border-control bg-surface text-ink-muted",
                )}
              >
                {done ? <Icon name="check" className="size-4" /> : position + 1}
              </span>
              <span className={cn("text-sm", now ? "font-semibold text-ink" : "text-ink-muted")}>
                {done ? <span className="sr-only">{t("done")}: </span> : null}
                {t(step)}
              </span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** Loading, error or "not available" for a single record (tables use DataTable instead). */
function LoadableNote({ state }: { state: Loadable<unknown> }) {
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  if (state.status === "loading") return <LoadingState label={tc("loading")} />;
  if (state.status === "unavailable") {
    return (
      <Alert tone="info" title={tc("notAvailableYetTitle")}>
        {tc("notAvailableYetBody")}
      </Alert>
    );
  }
  if (state.status === "error") {
    return (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {state.reason ? te(`load.${state.reason}`) : tc("loadErrorBody")}
      </Alert>
    );
  }
  return null;
}

function yearLabel(years: Loadable<readonly AcademicYear[]>, id: string): string | null {
  return years.status === "ready" ? (years.data.find((row) => row.id === id)?.label ?? null) : null;
}

/* -------------------------------------------------------------- status, history and undo */

/** When, and by whom (display name from the API; never an email). */
function WhenBy({
  when,
  name,
}: {
  when: string | null | undefined;
  name?: string | null | undefined;
}) {
  const t = useTranslations("academicStructure.promotions");
  const at = formatDateTime(when ?? null);
  if (!at) return <Value>{null}</Value>;
  return (
    <span>
      <span className="block">{at}</span>
      {name ? <span className="block text-sm text-ink-muted">{t("byName", { name })}</span> : null}
    </span>
  );
}

function StatusCard({
  from,
  runs,
  years,
}: {
  from: AcademicYear;
  runs: Loadable<readonly PromotionRun[]>;
  years: Loadable<readonly AcademicYear[]>;
}) {
  const t = useTranslations("academicStructure.promotions");
  const tc = useTranslations("common");
  if (runs.status !== "ready") {
    return (
      <Card title={t("statusTitle")}>
        <LoadableNote state={runs} />
      </Card>
    );
  }
  const current = committedRun(runs.data);
  const columns: Column<PromotionRun>[] = [
    {
      key: "status",
      header: t("colStatus"),
      cell: (run) => (
        <Badge tone={run.status === "committed" ? "success" : "neutral"}>
          {t(`runStatus.${run.status}`)}
        </Badge>
      ),
    },
    {
      key: "to",
      header: t("colTo"),
      cell: (run) => <Value>{yearLabel(years, run.to_academic_year_id)}</Value>,
    },
    {
      key: "committed",
      header: t("colCommitted"),
      cell: (run) => <WhenBy when={run.committed_at} name={run.committed_by_name} />,
    },
    {
      key: "undone",
      header: t("colUndone"),
      cell: (run) => <WhenBy when={run.undone_at} name={run.undone_by_name} />,
    },
    {
      key: "counts",
      header: t("colCounts"),
      cell: (run) => t("countsSummary", run.counts),
    },
  ];
  return (
    <Card title={t("statusTitle")}>
      <div className="space-y-4">
        {current ? (
          <>
            <Alert tone="success" title={t("doneTitle")}>
              <p>
                {t("doneBody", {
                  to: yearLabel(years, current.to_academic_year_id) ?? tc("notAvailable"),
                  when: formatDateTime(current.committed_at) ?? "",
                })}
              </p>
              <p>{t("countsSummary", current.counts)}</p>
            </Alert>
            {current.can_undo ? (
              <div className="flex flex-wrap items-center gap-3">
                <p className="text-sm">
                  {t("undoUntil", { when: formatDateTime(current.undo_until) ?? "" })}
                </p>
                <UndoDialog from={from} />
              </div>
            ) : (
              <Alert tone="info">{t("undoExpired")}</Alert>
            )}
          </>
        ) : (
          <p className="text-sm text-ink-muted">{t("notYet")}</p>
        )}
        {runs.data.length > 0 ? (
          <DataTable
            caption={t("historyTitle")}
            columns={columns}
            state={ready(runs.data)}
            rowKey={(run) => run.id}
            emptyTitle={t("notYet")}
          />
        ) : null}
      </div>
    </Card>
  );
}

function UndoDialog({ from }: { from: AcademicYear }) {
  const t = useTranslations("academicStructure.promotions");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("undo")}
      triggerVariant="danger"
      title={t("undoTitle")}
      description={t("undoBody", { from: from.label })}
      confirmLabel={t("undoConfirm")}
      confirmVariant="danger"
      schema={z.object({})}
      invalidate={AFTER_PROMOTION_KEYS}
      errorNamespace="academicStructure"
      submit={() => undoPromotion(api, from.id)}
    />
  );
}

/* ------------------------------------------------------------------------- the plan */

/** A section problem from a preview, kept so its choice stays visible after it is solved. */
interface Choice {
  fromSectionId: string;
  fromLabel: string;
  targetClassId: string;
}

function PlanCard({
  from,
  years,
  classes,
  onStage,
}: {
  from: AcademicYear;
  years: Loadable<readonly AcademicYear[]>;
  classes: Loadable<readonly SchoolClass[]>;
  /** Tells the step indicator whether a preview matches the plan. */
  onStage: (stage: PlanStage) => void;
}) {
  const t = useTranslations("academicStructure.promotions");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const canRead = can(STUDENT_READ);
  const targetSelectId = useId();
  const targets = years.status === "ready" ? targetYears(years.data, from) : [];
  const [chosenTarget, setChosenTarget] = useState<string | null>(null);
  const toYearId =
    chosenTarget && targets.some((row) => row.id === chosenTarget)
      ? chosenTarget
      : (targets[0]?.id ?? "");
  const [heldBack, setHeldBack] = useState<PromotionPlan["heldBack"]>([]);
  const [sectionMap, setSectionMap] = useState<Record<string, string>>({});
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [preview, setPreview] = useState<PromotionPreview | null>(null);
  const [previewedFor, setPreviewedFor] = useState<PromotionRequest | null>(null);
  const fromSections = useYearSections(from.id);
  const toSections = useYearSections(toYearId || null);

  const request = planRequest({ toYearId, heldBack, sectionMap });
  const stale = preview !== null && !sameRequest(previewedFor, request);

  const run = useApiMutation((body: PromotionRequest) => previewPromotion(api, from.id, body), {
    onSuccess: (result) => {
      setPreview(result);
      setChoices((current) => {
        const next = { ...current };
        for (const problem of result.problems) {
          next[mapKey(problem.from_section_id, problem.target_class_id)] = {
            fromSectionId: problem.from_section_id,
            fromLabel: problem.from_label,
            targetClassId: problem.target_class_id,
          };
        }
        return next;
      });
    },
  });

  // A preview with problems sends the office back to the plan (choose sections, preview again).
  const stage: PlanStage =
    preview === null || !preview.can_commit
      ? "plan"
      : stale || run.isPending
        ? "stale"
        : "previewed";
  useEffect(() => onStage(stage), [onStage, stage]);

  const onTarget = (id: string) => {
    // Sections belong to one year: choices for another target year no longer apply.
    setChosenTarget(id);
    setSectionMap({});
    setChoices({});
  };
  const toggleHeld = (id: string, label: string | null) =>
    setHeldBack((current) =>
      current.some((row) => row.id === id)
        ? current.filter((row) => row.id !== id)
        : [...current, { id, label }],
    );

  if (years.status !== "ready") return <LoadableNote state={years} />;
  if (targets.length === 0) {
    return (
      <Card title={t("planTitle")}>
        <Alert tone="info" title={t("noTargetTitle")}>
          <p>{t("noTargetBody", { label: from.label })}</p>
          <p className="mt-2">
            <ButtonLink href={STRUCTURE_HREF} variant="secondary" size="sm">
              {t("openStructure")}
            </ButtonLink>
          </p>
        </Alert>
      </Card>
    );
  }
  const toYear = targets.find((row) => row.id === toYearId);

  return (
    <>
      <Card title={t("planTitle")} description={t("planDescription")}>
        <div className="space-y-6">
          <div className="max-w-xs space-y-1">
            <Label htmlFor={targetSelectId}>{t("targetField")}</Label>
            <p id={`${targetSelectId}-hint`} className="text-sm text-ink-muted">
              {t("targetHint")}
            </p>
            <Select
              id={targetSelectId}
              aria-describedby={`${targetSelectId}-hint`}
              value={toYearId}
              onChange={(event) => onTarget(event.target.value)}
              options={targets.map((row) => ({ value: row.id, label: row.label }))}
            />
          </div>

          <HeldBackPicker
            from={from}
            canRead={canRead}
            sections={fromSections}
            heldBack={heldBack}
            onToggle={toggleHeld}
          />

          <SectionChoices
            choices={Object.values(choices)}
            sectionMap={sectionMap}
            onChoose={(key, value) => setSectionMap((current) => ({ ...current, [key]: value }))}
            toSections={toSections}
            classes={classes}
            toLabel={toYear?.label ?? ""}
          />

          <div className="flex flex-wrap items-center gap-3">
            {/* Keyed by variant: a new button, not a colour transition, when the main action
                moves to "Promote students" (no half-faded text for a moment). */}
            <Button
              key={preview && !stale ? "secondary" : "primary"}
              variant={preview && !stale ? "secondary" : "primary"}
              onClick={() => {
                setPreviewedFor(request);
                run.mutate(request);
              }}
              disabled={run.isPending}
              aria-disabled={run.isPending || undefined}
            >
              {run.isPending ? t("previewing") : t("preview")}
            </Button>
            <p className="text-sm text-ink-muted">{t("previewHint")}</p>
          </div>
          <ApiErrorAlert error={run.error ?? undefined} namespace="academicStructure" />
        </div>
      </Card>

      {preview ? (
        <PreviewCard
          from={from}
          toLabel={toYear?.label ?? ""}
          preview={preview}
          stale={stale || run.isPending}
          request={request}
          canRead={canRead}
          heldBack={heldBack}
          onToggle={toggleHeld}
          onCommitted={() => {
            setPreview(null);
            setPreviewedFor(null);
          }}
          onPlanChanged={() => setPreviewedFor(null)}
        />
      ) : null}
    </>
  );
}

/* ------------------------------------------------------------------ held-back students */

function HeldBackPicker({
  from,
  canRead,
  sections,
  heldBack,
  onToggle,
}: {
  from: AcademicYear;
  canRead: boolean;
  sections: Loadable<readonly Section[]>;
  heldBack: PromotionPlan["heldBack"];
  onToggle: (id: string, label: string | null) => void;
}) {
  const t = useTranslations("academicStructure.promotions");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const searchId = useId();
  const headingId = useId();
  const [query, setQuery] = useState("");
  const search = useApiMutation((text: string) => searchStudents(api, text));
  const inYear = new Set(sections.status === "ready" ? sections.data.map((row) => row.id) : []);
  const results = (search.data ?? []).filter(
    (row) => row.section_id !== null && inYear.has(row.section_id),
  );
  const held = new Set(heldBack.map((row) => row.id));

  const onSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = query.trim();
    if (text.length > 0) search.mutate(text);
  };

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h3 id={headingId} className="font-semibold">
        {t("heldBackTitle")}
      </h3>
      <p className="text-sm text-ink-muted">{t("heldBackHint")}</p>
      {canRead ? (
        <>
          <form role="search" onSubmit={onSearch} className="flex flex-wrap items-end gap-2">
            <div className="w-full max-w-sm space-y-1">
              <Label htmlFor={searchId}>{t("searchField")}</Label>
              <Input
                id={searchId}
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                autoComplete="off"
                maxLength={200}
                aria-describedby={`${searchId}-hint`}
              />
              <p id={`${searchId}-hint`} className="text-sm text-ink-muted">
                {from.is_current ? t("searchHint") : t("searchHintNotCurrent")}
              </p>
            </div>
            <Button type="submit" variant="secondary" disabled={search.isPending}>
              {search.isPending ? tc("working") : tc("search")}
            </Button>
          </form>
          <ApiErrorAlert error={search.error ?? undefined} />
          <div role="status" aria-live="polite" className="text-sm">
            {search.isSuccess ? t("searchResults", { count: results.length }) : null}
          </div>
          {results.length > 0 ? (
            <ul className="divide-y divide-border rounded-md border border-border">
              {results.map((student) => (
                <SearchResult
                  key={student.id}
                  student={student}
                  held={held.has(student.id)}
                  onToggle={onToggle}
                />
              ))}
            </ul>
          ) : null}
        </>
      ) : (
        <Alert tone="info">{t("noStudentRead")}</Alert>
      )}
      {heldBack.length > 0 ? (
        <div className="space-y-2">
          <p className="text-sm font-semibold">{t("heldBackCount", { count: heldBack.length })}</p>
          <ul className="flex flex-wrap gap-2">
            {heldBack.map((row) => {
              const name = row.label ?? t("nameHidden");
              return (
                <li
                  key={row.id}
                  className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-muted py-1 pr-1 pl-3 text-sm"
                >
                  <span>{name}</span>
                  <Button variant="ghost" size="sm" onClick={() => onToggle(row.id, row.label)}>
                    {t("remove")} <span className="sr-only">{t("aboutStudent", { name })}</span>
                  </Button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function SearchResult({
  student,
  held,
  onToggle,
}: {
  student: StudentSummary;
  held: boolean;
  onToggle: (id: string, label: string | null) => void;
}) {
  const t = useTranslations("academicStructure.promotions");
  const label = studentLabel(student);
  const name = label ?? t("nameHidden");
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
      <span>
        {name}
        {student.class_section ? (
          <span className="text-ink-muted"> · {student.class_section}</span>
        ) : null}
      </span>
      <HoldToggle held={held} name={name} onClick={() => onToggle(student.id, label)} />
    </li>
  );
}

/**
 * "Keep in class" toggle (aria-pressed; the name stays the same, the state is announced).
 * The student's name is added for screen readers only, after the visible words.
 */
function HoldToggle({ held, name, onClick }: { held: boolean; name: string; onClick: () => void }) {
  const t = useTranslations("academicStructure.promotions");
  return (
    <Button
      variant={held ? "primary" : "secondary"}
      size="sm"
      onClick={onClick}
      aria-pressed={held}
    >
      {held ? <span aria-hidden="true">✓</span> : null}
      {t("holdBack")} <span className="sr-only">{t("aboutStudent", { name })}</span>
    </Button>
  );
}

/* --------------------------------------------------------------------- section choices */

function SectionChoices({
  choices,
  sectionMap,
  onChoose,
  toSections,
  classes,
  toLabel,
}: {
  choices: readonly Choice[];
  sectionMap: Readonly<Record<string, string>>;
  onChoose: (key: string, toSectionId: string) => void;
  toSections: Loadable<readonly Section[]>;
  classes: Loadable<readonly SchoolClass[]>;
  toLabel: string;
}) {
  const t = useTranslations("academicStructure.promotions");
  const locale = useLocale();
  const headingId = useId();
  const baseId = useId();
  if (choices.length === 0) return null;
  const className = (id: string): string => {
    const row = classes.status === "ready" ? classes.data.find((c) => c.id === id) : undefined;
    return row ? classLabel(row, locale) : t("unknownClass");
  };
  const sections = toSections.status === "ready" ? toSections.data : [];
  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h3 id={headingId} className="font-semibold">
        {t("mapTitle")}
      </h3>
      <p className="text-sm text-ink-muted">{t("mapHint", { year: toLabel })}</p>
      <ul className="space-y-3">
        {choices.map((choice, index) => {
          const key = mapKey(choice.fromSectionId, choice.targetClassId);
          const options = sections
            .filter((row) => row.class_id === choice.targetClassId)
            .map((row) => ({ value: row.id, label: `${className(row.class_id)} ${row.name}` }));
          const id = `${baseId}-${index}`;
          const target = className(choice.targetClassId);
          return (
            <li key={key} className="max-w-md space-y-1">
              <Label htmlFor={id}>
                {t("mapField", { from: choice.fromLabel, className: target })}
              </Label>
              {toSections.status === "ready" && options.length === 0 ? (
                <p id={id} className="text-sm text-ink-muted">
                  {t("mapNone", { className: target, year: toLabel })}
                </p>
              ) : (
                <Select
                  id={id}
                  value={sectionMap[key] ?? ""}
                  onChange={(event) => onChoose(key, event.target.value)}
                  placeholder={t("mapPlaceholder")}
                  options={options}
                />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ---------------------------------------------------------------------------- preview */

function PreviewCard({
  from,
  toLabel,
  preview,
  stale,
  request,
  canRead,
  heldBack,
  onToggle,
  onCommitted,
  onPlanChanged,
}: {
  from: AcademicYear;
  toLabel: string;
  preview: PromotionPreview;
  stale: boolean;
  request: PromotionRequest;
  canRead: boolean;
  heldBack: PromotionPlan["heldBack"];
  onToggle: (id: string, label: string | null) => void;
  onCommitted: () => void;
  onPlanChanged: () => void;
}) {
  const t = useTranslations("academicStructure.promotions");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const counts = preview.counts;
  const moving = counts.promoted + counts.held_back + counts.graduated;
  const groupColumns: Column<PromotionPreview["groups"][number]>[] = [
    { key: "from", header: t("colFrom"), cell: (row) => row.from_label },
    {
      key: "outcome",
      header: t("colOutcome"),
      cell: (row) => <Badge tone={OUTCOME_TONE[row.outcome]}>{t(`outcome.${row.outcome}`)}</Badge>,
    },
    {
      key: "to",
      header: t("colToSection"),
      cell: (row) => (row.to_label ? <TargetChip label={row.to_label} /> : <Value>{null}</Value>),
    },
    { key: "count", header: t("colStudents"), numeric: true, cell: (row) => row.count },
  ];
  const problemCount = preview.problems.reduce((sum, row) => sum + row.count, 0);

  return (
    <Card title={t("previewTitle", { from: from.label, to: toLabel })}>
      <div className="space-y-6">
        {stale ? (
          <Alert tone="warning" live>
            {t("stale")}
          </Alert>
        ) : null}
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {(["promoted", "held_back", "graduated", "skipped"] as const).map((key) => (
            <StatCard
              key={key}
              label={t(`outcome.${key}`)}
              value={String(counts[key])}
              unavailableLabel={tc("notAvailable")}
            />
          ))}
        </dl>
        {preview.problems.length > 0 ? (
          <Alert tone="warning" title={t("problemsTitle", { count: problemCount })}>
            <ul className="list-disc pl-5">
              {preview.problems.map((row) => (
                <li key={`${row.from_section_id}|${row.target_class_id}`}>
                  {t("problemLine", { from: row.from_label, count: row.count })}
                </li>
              ))}
            </ul>
            <p className="mt-2">{t("problemsFix")}</p>
          </Alert>
        ) : null}
        <DataTable
          caption={t("groupsTitle")}
          columns={groupColumns}
          state={ready(preview.groups)}
          rowKey={(row) => `${row.from_section_id}|${row.outcome}|${row.to_section_id ?? ""}`}
          emptyTitle={t("groupsEmpty")}
        />
        <StudentOutcomes
          preview={preview}
          canRead={canRead}
          heldBack={heldBack}
          onToggle={onToggle}
        />
        <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
          <ActionDialog
            triggerLabel={t("commit")}
            triggerVariant="primary"
            triggerDisabled={stale || !preview.can_commit}
            title={t("commitTitle", { count: moving })}
            description={t("commitBody", {
              from: from.label,
              to: toLabel,
              promoted: counts.promoted,
              held: counts.held_back,
              graduated: counts.graduated,
            })}
            confirmLabel={t("commitConfirm")}
            schema={z.object({})}
            invalidate={AFTER_PROMOTION_KEYS}
            errorNamespace="academicStructure"
            onSuccess={onCommitted}
            submit={async (_data, key) => {
              try {
                return await commitPromotion(api, from.id, request, preview.plan_fingerprint, key);
              } catch (error) {
                if (error instanceof ApiError && MUST_PREVIEW_AGAIN.has(error.code ?? "")) {
                  onPlanChanged();
                }
                throw error;
              }
            }}
          />
          <p className="text-sm text-ink-muted">
            {stale
              ? t("commitNeedsPreview")
              : preview.can_commit
                ? t("commitHint")
                : t("cannotCommit")}
          </p>
        </div>
      </div>
    </Card>
  );
}

/** The section a student or group moves to, as a light-blue chip. */
function TargetChip({ label }: { label: string }) {
  return (
    <Pill variant="date">
      <Icon name="arrowRight" className="size-3" />
      {label}
    </Pill>
  );
}

const MUST_PREVIEW_AGAIN = new Set(["promotion_plan_changed", "no_target_section"]);

/**
 * Per-student outcomes for one section at a time. Names come from the student list (only
 * with `student.read_basic`); without them no name or id is shown, only the outcome.
 */
function StudentOutcomes({
  preview,
  canRead,
  heldBack,
  onToggle,
}: {
  preview: PromotionPreview;
  canRead: boolean;
  heldBack: PromotionPlan["heldBack"];
  onToggle: (id: string, label: string | null) => void;
}) {
  const t = useTranslations("academicStructure.promotions");
  const locale = useLocale();
  const selectId = useId();
  const sectionLabels = useMemo(() => {
    const labels = new Map<string, string>();
    for (const group of preview.groups) labels.set(group.from_section_id, group.from_label);
    for (const problem of preview.problems) labels.set(problem.from_section_id, problem.from_label);
    return labels;
  }, [preview]);
  const toLabels = useMemo(() => {
    const labels = new Map<string, string>();
    for (const group of preview.groups) {
      if (group.to_section_id && group.to_label) labels.set(group.to_section_id, group.to_label);
    }
    return labels;
  }, [preview]);
  const sectionIds = [...new Set(preview.students.map((row) => row.from_section_id))].sort((a, b) =>
    (sectionLabels.get(a) ?? "").localeCompare(sectionLabels.get(b) ?? "", locale, {
      numeric: true,
    }),
  );
  const [chosen, setChosen] = useState<string | null>(null);
  const sectionId = chosen && sectionIds.includes(chosen) ? chosen : (sectionIds[0] ?? null);
  const names = useSectionStudents(sectionId, canRead);
  const byId = new Map(
    names.status === "ready" ? names.data.map((row) => [row.id, row] as const) : [],
  );
  const held = new Set(heldBack.map((row) => row.id));
  const rows = preview.students
    .filter((row) => row.from_section_id === sectionId)
    .map((row) => ({ ...row, label: studentLabel(byId.get(row.student_id)) }))
    .sort((a, b) => (a.label ?? "￿").localeCompare(b.label ?? "￿", locale));

  const nameCell = (row: (typeof rows)[number]) => {
    if (!canRead) return <span className="text-ink-muted">{t("nameHidden")}</span>;
    if (names.status === "loading")
      return <span className="text-ink-muted">{t("nameLoading")}</span>;
    return row.label ?? <span className="text-ink-muted">{t("nameNotAvailable")}</span>;
  };
  const columns: Column<(typeof rows)[number]>[] = [
    { key: "name", header: t("colStudent"), cell: nameCell },
    {
      key: "outcome",
      header: t("colOutcome"),
      cell: (row) => <Badge tone={OUTCOME_TONE[row.outcome]}>{t(`outcome.${row.outcome}`)}</Badge>,
    },
    {
      key: "to",
      header: t("colToSection"),
      cell: (row) => {
        const label = row.to_section_id ? toLabels.get(row.to_section_id) : undefined;
        return label ? <TargetChip label={label} /> : <Value>{null}</Value>;
      },
    },
    { key: "note", header: t("colNote"), cell: (row) => <ReasonText student={row} /> },
    ...(canRead
      ? [
          {
            key: "action",
            header: t("colAction"),
            cell: (row: (typeof rows)[number]) =>
              row.outcome === "skipped" || row.label === null ? null : (
                <HoldToggle
                  held={held.has(row.student_id)}
                  name={row.label}
                  onClick={() => onToggle(row.student_id, row.label)}
                />
              ),
          },
        ]
      : []),
  ];

  if (sectionIds.length === 0) return null;
  return (
    <section aria-labelledby={`${selectId}-heading`} className="space-y-3">
      <h3 id={`${selectId}-heading`} className="font-semibold">
        {t("studentsTitle")}
      </h3>
      {!canRead ? <p className="text-sm text-ink-muted">{t("namesHiddenNote")}</p> : null}
      <div className="max-w-xs space-y-1">
        <Label htmlFor={selectId}>{t("sectionField")}</Label>
        <Select
          id={selectId}
          value={sectionId ?? ""}
          onChange={(event) => setChosen(event.target.value)}
          options={sectionIds.map((id) => ({
            value: id,
            label: sectionLabels.get(id) ?? t("unknownSection"),
          }))}
        />
      </div>
      <DataTable
        caption={t("studentsCaption", { section: sectionLabels.get(sectionId ?? "") ?? "" })}
        columns={columns}
        state={ready(rows)}
        rowKey={(row) => row.enrollment_id}
        emptyTitle={t("groupsEmpty")}
      />
    </section>
  );
}

const REASONS = ["left", "graduated", "already_enrolled", "no_target_section"] as const;
type Reason = (typeof REASONS)[number];

function ReasonText({ student }: { student: PromotionStudent }) {
  const t = useTranslations("academicStructure.promotions.reason");
  const reason = student.reason;
  if (!reason) return <Value>{null}</Value>;
  return <>{(REASONS as readonly string[]).includes(reason) ? t(reason as Reason) : t("other")}</>;
}
