"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SaveBar } from "@/components/ui/SaveBar";
import { Select, SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { useSectionOptions } from "@/features/findings/data";
import type { Locale } from "@/i18n/routing";
import { useApiMutation, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount } from "@/lib/format";
import { IssueList, labelOr, looseT } from "./IssueList";
import {
  CANDIDATE_EDIT,
  CANDIDATE_KEYS,
  draftOf,
  entryFor,
  fetchSectionGrid,
  fillBlanks,
  saveSectionGrid,
  type BoardScheme,
  type CandidateEntry,
  type CandidateGrid,
  type Draft,
  type SingleKey,
} from "./data";

const READ = "student.read_basic";

function mediumLabel(t: unknown, medium: string): string {
  return labelOr(looseT(t), "medium", medium);
}

/** The section's candidates, one row each, with what is still missing for the board. */
function Grid({ grid, canEdit }: { grid: CandidateGrid; canEdit: boolean }) {
  const t = useTranslations("boardCandidates");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const scheme = grid.scheme as BoardScheme;
  const recorded = useMemo(
    () => Object.fromEntries(grid.rows.map((row) => [row.student_id, draftOf(scheme, row.values)])),
    [grid, scheme],
  );
  const [drafts, setDrafts] = useState<Record<string, Draft>>(recorded);
  const [source, setSource] = useState<"manual_entry" | "parent_form">("parent_form");
  const [fill, setFill] = useState<Partial<Record<SingleKey, string>>>({});
  const [saved, setSaved] = useState<{ recorded: number } | null>(null);
  // A new answer (after a save or a reload) resets the drafts (state adjusted during render).
  const [base, setBase] = useState(recorded);
  if (base !== recorded) {
    setBase(recorded);
    setDrafts(recorded);
  }

  const entries = grid.rows
    .map((row) => {
      const before = recorded[row.student_id];
      const after = drafts[row.student_id];
      return before && after ? entryFor(scheme, row.student_id, before, after) : null;
    })
    .filter((entry): entry is CandidateEntry => entry !== null);
  const dirty = entries.length > 0;
  const save = useApiMutation(
    () => saveSectionGrid(api, grid.section_id, { scheme: scheme.key, source, rows: entries }),
    {
      onSuccess: (out) => {
        setSaved({ recorded: out.recorded });
        queryClient.setQueryData(CANDIDATE_KEYS.section(grid.section_id, scheme.key), out.grid);
      },
      invalidate: [CANDIDATE_KEYS.all],
    },
  );

  function set(studentId: string, change: Partial<Draft>) {
    setSaved(null);
    setDrafts((current) => {
      const draft = current[studentId];
      return draft ? { ...current, [studentId]: { ...draft, ...change } } : current;
    });
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (dirty) save.mutate(undefined);
  }

  const mediumOptions = scheme.medium_options.map((m) => ({ value: m, label: mediumLabel(t, m) }));
  const slotLabel = (slot: string) => t(`slot.${slot}` as Parameters<typeof t>[0]);

  return (
    <form onSubmit={onSubmit} aria-label={t("formLabel")}>
      <Card
        title={t("gridTitle", { section: grid.class_section ?? "" })}
        description={t("readyCount", {
          ready: formatCount(grid.ready_count, locale) ?? "",
          total: formatCount(grid.rows.length, locale) ?? "",
        })}
      >
        <div className="space-y-4">
          {!scheme.verified ? (
            <Alert tone="info" title={t("unverifiedTitle")}>
              {t("unverifiedBody")} {scheme.source_note}
            </Alert>
          ) : null}
          {canEdit ? (
            <fieldset className="flex flex-wrap items-end gap-3 rounded-lg border border-border p-3">
              <legend className="px-1 text-sm font-semibold text-ink">{t("fillTitle")}</legend>
              <SelectField
                label={t("medium.label")}
                className="w-44"
                value={fill.exam_medium ?? ""}
                placeholder={t("choose")}
                options={mediumOptions}
                onChange={(event) => setFill({ ...fill, exam_medium: event.target.value })}
              />
              {scheme.languages.map((slot) => (
                <SelectField
                  key={slot.slot}
                  label={slotLabel(slot.slot)}
                  className="w-52"
                  value={fill[slot.slot as SingleKey] ?? ""}
                  placeholder={t("choose")}
                  options={slot.options.map((o) => ({ value: o.code, label: o.label }))}
                  onChange={(event) => setFill({ ...fill, [slot.slot]: event.target.value })}
                />
              ))}
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setSaved(null);
                  setDrafts((current) => fillBlanks(current, fill));
                }}
              >
                {t("fillButton")}
              </Button>
              <p className="basis-full text-sm text-ink-muted">{t("fillHint")}</p>
            </fieldset>
          ) : null}
          {grid.rows.length === 0 ? (
            <EmptyState title={t("noStudentsTitle")} body={t("noStudentsBody")} />
          ) : (
            <TableScroll label={t("tableLabel")}>
              <Table stickyFirstColumn density="compact">
                <caption className="sr-only">{t("tableLabel")}</caption>
                <THead>
                  <Tr>
                    <Th scope="col">{t("colStudent")}</Th>
                    <Th scope="col">{t("medium.label")}</Th>
                    {scheme.languages.map((slot) => (
                      <Th scope="col" key={slot.slot}>
                        {slotLabel(slot.slot)}
                      </Th>
                    ))}
                    {scheme.subjects.map((group) => (
                      <Th scope="col" key={group.key}>
                        {group.label_en}
                      </Th>
                    ))}
                    <Th scope="col">{t("colPhoto")}</Th>
                    <Th scope="col">{t("colStatus")}</Th>
                  </Tr>
                </THead>
                <TBody>
                  {grid.rows.map((row) => {
                    const draft = drafts[row.student_id];
                    if (!draft) return null;
                    const name = row.display_name ?? t("unnamed");
                    const who = (field: string) => `${field}: ${name}`;
                    return (
                      <Tr key={row.student_id}>
                        <Th scope="row">
                          <span className="block">{name}</span>
                          <span className="block font-mono text-xs text-ink-muted">
                            {[
                              row.roll_no ? t("roll", { roll: row.roll_no }) : null,
                              row.admission_no,
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </span>
                        </Th>
                        <Td>
                          <Select
                            aria-label={who(t("medium.label"))}
                            disabled={!canEdit}
                            value={draft.exam_medium}
                            placeholder={t("choose")}
                            options={mediumOptions}
                            onChange={(event) =>
                              set(row.student_id, { exam_medium: event.target.value })
                            }
                          />
                        </Td>
                        {scheme.languages.map((slot) => (
                          <Td key={slot.slot}>
                            <Select
                              aria-label={who(slotLabel(slot.slot))}
                              disabled={!canEdit}
                              value={draft[slot.slot as SingleKey]}
                              placeholder={t("choose")}
                              options={slot.options.map((o) => ({
                                value: o.code,
                                label: `${o.label} (${o.code})`,
                              }))}
                              onChange={(event) =>
                                set(row.student_id, { [slot.slot]: event.target.value })
                              }
                            />
                          </Td>
                        ))}
                        {scheme.subjects.map((group) => (
                          <Td key={group.key}>
                            <Select
                              aria-label={who(group.label_en)}
                              disabled={!canEdit}
                              value={draft.subjects[group.key] ?? ""}
                              placeholder={group.required ? t("choose") : t("none")}
                              options={group.options.map((o) => ({
                                value: o.code,
                                label: `${o.label} (${o.code})`,
                              }))}
                              onChange={(event) =>
                                set(row.student_id, {
                                  subjects: { ...draft.subjects, [group.key]: event.target.value },
                                })
                              }
                            />
                          </Td>
                        ))}
                        <Td>
                          <label className="inline-flex min-h-10 items-center gap-2 text-sm">
                            <input
                              type="checkbox"
                              className="size-4 accent-primary"
                              disabled={!canEdit}
                              checked={draft.photo}
                              onChange={(event) =>
                                set(row.student_id, { photo: event.target.checked })
                              }
                            />
                            <span>{t("photoCollected")}</span>
                            <span className="sr-only">: {name}</span>
                          </label>
                        </Td>
                        <Td>
                          {row.ready ? (
                            <Badge tone="success">{t("ready")}</Badge>
                          ) : (
                            <IssueList issues={row.issues} scheme={scheme} />
                          )}
                        </Td>
                      </Tr>
                    );
                  })}
                </TBody>
              </Table>
            </TableScroll>
          )}
          {save.error ? <ApiErrorAlert error={save.error} /> : null}
          {saved ? (
            <Alert tone="success" live>
              {t("saved", { count: saved.recorded })}
            </Alert>
          ) : null}
          {canEdit && grid.rows.length > 0 ? (
            <SaveBar dirty={dirty}>
              <div className="flex flex-wrap items-center gap-3">
                <SelectField
                  label={t("sourceLabel")}
                  className="w-56"
                  value={source}
                  options={[
                    { value: "parent_form", label: t("source.parent_form") },
                    { value: "manual_entry", label: t("source.manual_entry") },
                  ]}
                  onChange={(event) =>
                    setSource(
                      event.target.value === "manual_entry" ? "manual_entry" : "parent_form",
                    )
                  }
                />
                <Button
                  type="button"
                  variant="secondary"
                  disabled={!dirty || save.isPending}
                  onClick={() => setDrafts(recorded)}
                >
                  {t("discard")}
                </Button>
                <Button type="submit" disabled={!dirty || save.isPending}>
                  {t("save", { count: entries.length })}
                </Button>
              </div>
            </SaveBar>
          ) : null}
        </div>
      </Card>
    </form>
  );
}

/**
 * Board candidates (FO-10, FR-STU-026..029): the office records each candidate's medium,
 * languages, subject choices and whether the photograph and signature were collected, per
 * section, from the parent's form. Never a photo upload: SchoolOS does not hold them.
 */
export function BoardCandidatesScreen() {
  const t = useTranslations("boardCandidates");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(READ);
  const canEdit = can(CANDIDATE_EDIT);
  const { sections, loading } = useSectionOptions();
  const [sectionId, setSectionId] = useState("");
  const [scheme, setScheme] = useState("");
  const grid = useQuery({
    queryKey: CANDIDATE_KEYS.section(sectionId, scheme),
    queryFn: () => fetchSectionGrid(api, sectionId, scheme),
    enabled: allowed && sectionId !== "",
    retry: false,
  });
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
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
  const data = grid.data;
  return (
    <div className="space-y-5">
      {header}
      <div className="flex flex-wrap items-end gap-3">
        <SelectField
          label={t("section")}
          className="w-64"
          value={sectionId}
          disabled={loading}
          placeholder={t("chooseSection")}
          options={sections.map((s) => ({ value: s.id, label: s.label }))}
          onChange={(event) => {
            setSectionId(event.target.value);
            setScheme("");
          }}
        />
        {data && data.available_schemes.length > 1 ? (
          <SelectField
            label={t("scheme")}
            className="w-72"
            value={scheme || (data.scheme?.key ?? "")}
            options={data.available_schemes.map((key) => ({
              value: key,
              label: labelOr(looseT(t), "schemes", key),
            }))}
            onChange={(event) => setScheme(event.target.value)}
          />
        ) : null}
      </div>
      {sectionId === "" ? (
        <EmptyState title={t("pickTitle")} body={t("pickBody")} />
      ) : grid.error ? (
        <ApiErrorAlert error={grid.error} />
      ) : !data ? (
        <LoadingState label={tc("loading")} />
      ) : data.scheme === null ? (
        <EmptyState title={t("noSchemeTitle")} body={t("noSchemeBody")} />
      ) : (
        <Grid key={`${data.section_id}:${data.scheme.key}`} grid={data} canEdit={canEdit} />
      )}
    </div>
  );
}
