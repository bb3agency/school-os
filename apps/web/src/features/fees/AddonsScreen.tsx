"use client";

import { useTranslations } from "next-intl";
import { useMemo, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { useSectionOptions } from "@/features/findings/data";
import {
  newIdempotencyKey,
  unwrap,
  useApiMutation,
  useApiQuery,
  useBffClient,
} from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import {
  ADDON_HEAD_KINDS,
  ADDON_KEYS,
  ADDON_READ,
  addonApi,
  amountsOf,
  type Addon,
  type AddonAssignResult,
  type AddonTarget,
  type AddonUnassignResult,
} from "./addons";
import { FINANCE_READ, KEYS, MANAGE, REASON_MAX, REASON_MIN, type AccountSummary } from "./data";
import { Amount, FeesGate, NoAccess } from "./parts";

/**
 * Add-on fees (FM-02; FR-FEE-081..100; D85): transport bands, hostel and activities charged per
 * student on top of the class plan. The accountant sets up templates and puts a class, a section
 * or picked students on one from an instalment; taking students off proposes credits that
 * someone else approves (only a reduction needs maker-checker). A transport in-charge with
 * `fee.addon.read` sees who is on each add-on, never the amounts.
 */
export function AddonsScreen() {
  const t = useTranslations("fees.addons");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([ADDON_READ, FINANCE_READ, MANAGE]);
  const manager = can(MANAGE);
  const api = useBffClient("staff");
  const calls = useMemo(() => addonApi(api), [api]);
  const list = useApiQuery(ADDON_KEYS.list, () => calls.list(), {
    enabled: me !== undefined && allowed,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
    />
  );
  if (me !== undefined && !allowed) {
    return (
      <div className="space-y-5">
        {header}
        <NoAccess />
      </div>
    );
  }
  return (
    <div className="space-y-5">
      {header}
      <FeesGate data={list}>
        {(data) => {
          const current = data.items.find((item) => item.id === selected) ?? null;
          return (
            <>
              {manager ? (
                <Alert tone="info" title={t("gstTitle")}>
                  {data.gst_note}
                </Alert>
              ) : null}
              <Card title={t("listTitle")} padding="none">
                {data.items.length === 0 ? (
                  <p className="p-5 text-sm text-ink-muted">{t("empty")}</p>
                ) : (
                  <TableScroll label={t("listTitle")}>
                    <Table>
                      <THead>
                        <Tr>
                          <Th>{t("columns.label")}</Th>
                          <Th>{t("columns.head")}</Th>
                          {data.amounts_shown ? <Th numeric>{t("columns.total")}</Th> : null}
                          <Th numeric>{t("columns.students")}</Th>
                          <Th>{t("columns.status")}</Th>
                          <Th>
                            <span className="sr-only">{t("columns.actions")}</span>
                          </Th>
                        </Tr>
                      </THead>
                      <TBody>
                        {data.items.map((item) => (
                          <Tr key={item.id}>
                            <Td className="font-semibold">{item.label}</Td>
                            <Td>{item.head_name}</Td>
                            {data.amounts_shown ? (
                              <Td numeric>
                                <Amount value={item.total} />
                              </Td>
                            ) : null}
                            <Td numeric>{item.assigned}</Td>
                            <Td>
                              <Pill variant={item.active ? "done" : "tag"} size="sm" icon={null}>
                                {item.active ? t("active") : t("inactive")}
                              </Pill>
                            </Td>
                            <Td>
                              <Button
                                variant="secondary"
                                size="sm"
                                aria-pressed={selected === item.id}
                                onClick={() => setSelected(item.id)}
                              >
                                {t("open", { label: item.label })}
                              </Button>
                            </Td>
                          </Tr>
                        ))}
                      </TBody>
                    </Table>
                  </TableScroll>
                )}
              </Card>
              {current ? (
                <AddonPanel addon={current} manager={manager} amountsShown={data.amounts_shown} />
              ) : null}
              {manager ? <CreateAddon /> : null}
            </>
          );
        }}
      </FeesGate>
    </div>
  );
}

/** A new template: head, label and the amount per instalment (FR-FEE-081, FR-FEE-082). */
function CreateAddon() {
  const t = useTranslations("fees.addons.create");
  const api = useBffClient("staff");
  const calls = useMemo(() => addonApi(api), [api]);
  const heads = useApiQuery(KEYS.heads, () => unwrap(api.GET("/api/v1/fees/heads")));
  const years = useApiQuery(KEYS.years, () =>
    unwrap(api.GET("/api/v1/academic-years", { params: { query: { limit: 200 } } })),
  );
  const [headId, setHeadId] = useState("");
  const [label, setLabel] = useState("");
  const [values, setValues] = useState<string[]>(["", ""]);
  const [invalid, setInvalid] = useState(false);
  const [key] = useState(newIdempotencyKey);
  const create = useApiMutation(
    (body: Parameters<typeof calls.create>[0]) => calls.create(body, key),
    {
      invalidate: [ADDON_KEYS.all],
      onSuccess: () => {
        setLabel("");
        setValues(["", ""]);
      },
    },
  );
  const options =
    heads.status === "ready"
      ? heads.data
          .filter((h) => h.active && h.gst_exempt && ADDON_HEAD_KINDS.includes(h.kind))
          .map((h) => ({ value: h.id, label: `${h.name} (${h.code})` }))
      : [];
  const year =
    years.status === "ready" ? (years.data.data.find((y) => y.is_current) ?? null) : null;
  function submit(event: FormEvent) {
    event.preventDefault();
    const amounts = amountsOf(values);
    setInvalid(amounts === null);
    if (amounts === null || !year || !headId || !label.trim()) return;
    create.mutate({ academic_year_id: year.id, head_id: headId, label: label.trim(), amounts });
  }
  return (
    <Card title={t("title")} description={t("description")}>
      <form className="space-y-4" onSubmit={submit} noValidate>
        <div className="grid gap-4 md:grid-cols-2">
          <SelectField
            label={t("head")}
            hint={t("headHint")}
            placeholder={t("headPlaceholder")}
            options={options}
            value={headId}
            onChange={(event) => setHeadId(event.target.value)}
            required
          />
          <TextField
            label={t("label")}
            hint={t("labelHint")}
            value={label}
            maxLength={80}
            onChange={(event) => setLabel(event.target.value)}
            required
          />
        </div>
        <fieldset className="space-y-3">
          <legend className="text-sm font-semibold">{t("amounts")}</legend>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {values.map((value, index) => (
              <TextField
                key={index}
                label={t("instalment", { no: index + 1 })}
                inputMode="decimal"
                value={value}
                onChange={(event) =>
                  setValues((rows) =>
                    rows.map((row, i) => (i === index ? event.target.value : row)),
                  )
                }
              />
            ))}
          </div>
          {invalid ? (
            <p className="text-sm font-semibold text-danger" role="alert">
              {t("amountsInvalid")}
            </p>
          ) : null}
          {values.length < 24 ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setValues((rows) => [...rows, ""])}
            >
              {t("addInstalment")}
            </Button>
          ) : null}
        </fieldset>
        <ApiErrorAlert error={create.error} />
        <Button type="submit" loading={create.isPending} disabled={!year}>
          {t("submit")}
        </Button>
      </form>
    </Card>
  );
}

type Mode = "class" | "section" | "students";

/** Who is on one add-on, and (for managers) putting students on or taking them off. */
function AddonPanel({
  addon,
  manager,
  amountsShown,
}: {
  addon: Addon;
  manager: boolean;
  amountsShown: boolean;
}) {
  const t = useTranslations("fees.addons.panel");
  const api = useBffClient("staff");
  const calls = useMemo(() => addonApi(api), [api]);
  const students = useApiQuery(ADDON_KEYS.students(addon.id), () => calls.students(addon.id));
  return (
    <Card
      title={t("title", { label: addon.label })}
      description={t("description", { head: addon.head_name })}
      actions={
        <Button variant="secondary" size="sm" onClick={() => window.print()}>
          {t("print")}
        </Button>
      }
    >
      <div className="space-y-5">
        {amountsShown && addon.amounts ? (
          <p className="text-sm">
            {Object.entries(addon.amounts).map(([no, value]) => (
              <span key={no} className="me-4 inline-block">
                {t("perInstalment", { no })} <Amount value={value} />
              </span>
            ))}
          </p>
        ) : null}
        <FeesGate data={students}>
          {(data) =>
            data.items.length === 0 ? (
              <p className="text-sm text-ink-muted">{t("nobody")}</p>
            ) : (
              <TableScroll label={t("studentsLabel")}>
                <Table>
                  <THead>
                    <Tr>
                      <Th>{t("columns.student")}</Th>
                      <Th>{t("columns.class")}</Th>
                      <Th numeric>{t("columns.from")}</Th>
                      {data.amounts_shown ? <Th numeric>{t("columns.charged")}</Th> : null}
                    </Tr>
                  </THead>
                  <TBody>
                    {data.items.map((row) => (
                      <Tr key={row.assignment_id}>
                        <Td>
                          <span className="font-semibold">{row.display_name ?? t("noName")}</span>
                          <span className="block font-mono text-xs text-ink-muted">
                            {row.admission_no}
                          </span>
                        </Td>
                        <Td>{row.class_section ?? "—"}</Td>
                        <Td numeric>{row.start_instalment}</Td>
                        {data.amounts_shown ? (
                          <Td numeric>
                            <Amount value={row.charged} />
                          </Td>
                        ) : null}
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </TableScroll>
            )
          }
        </FeesGate>
        {manager ? <AssignForm addon={addon} /> : null}
      </div>
    </Card>
  );
}

/** Assign or take off a class, a section or picked students (FR-FEE-084, FR-FEE-089). */
function AssignForm({ addon }: { addon: Addon }) {
  const t = useTranslations("fees.addons.assign");
  const api = useBffClient("staff");
  const calls = useMemo(() => addonApi(api), [api]);
  const { sections } = useSectionOptions();
  const classes = useMemo(() => {
    const seen = new Map<string, string>();
    for (const s of sections) if (!seen.has(s.classId)) seen.set(s.classId, s.classLabel);
    return [...seen].map(([value, label]) => ({ value, label }));
  }, [sections]);
  const [action, setAction] = useState<"assign" | "unassign">("assign");
  const [mode, setMode] = useState<Mode>("section");
  const [classId, setClassId] = useState("");
  const [sectionId, setSectionId] = useState("");
  const [picked, setPicked] = useState<AccountSummary[]>([]);
  const [instalment, setInstalment] = useState("1");
  const [replace, setReplace] = useState(false);
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<AddonAssignResult | AddonUnassignResult | null>(null);
  const target = (): AddonTarget | null => {
    if (mode === "class") return classId ? { class_id: classId } : null;
    if (mode === "section") return sectionId ? { section_ids: [sectionId] } : null;
    return picked.length ? { student_ids: picked.map((p) => p.student_id) } : null;
  };
  const run = useApiMutation(
    async (dryRun: boolean) => {
      const whom = target();
      if (!whom) throw new Error("no target");
      const no = Number(instalment);
      return action === "assign"
        ? calls.assign(addon.id, {
            ...whom,
            start_instalment: no,
            dry_run: dryRun,
            ...(replace ? { replace: true, reason: reason.trim() } : {}),
          })
        : calls.unassign(addon.id, {
            ...whom,
            from_instalment: no,
            reason: reason.trim(),
            dry_run: dryRun,
          });
    },
    { invalidate: [ADDON_KEYS.all, KEYS.approvals], onSuccess: setResult },
  );
  const needsReason = action === "unassign" || replace;
  const reasonOk = !needsReason || reason.trim().length >= REASON_MIN;
  const ready = target() !== null && reasonOk;
  return (
    <form
      className="space-y-4 border-t border-border pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) run.mutate(false);
      }}
    >
      <h3 className="text-base font-semibold">{t("title")}</h3>
      <div className="grid gap-4 md:grid-cols-3">
        <SelectField
          label={t("action")}
          options={[
            { value: "assign", label: t("actions.assign") },
            { value: "unassign", label: t("actions.unassign") },
          ]}
          value={action}
          onChange={(event) => {
            setAction(event.target.value === "unassign" ? "unassign" : "assign");
            setResult(null);
          }}
        />
        <SelectField
          label={t("whom")}
          options={[
            { value: "class", label: t("modes.class") },
            { value: "section", label: t("modes.section") },
            { value: "students", label: t("modes.students") },
          ]}
          value={mode}
          onChange={(event) => setMode(event.target.value as Mode)}
        />
        <TextField
          label={action === "assign" ? t("startInstalment") : t("fromInstalment")}
          hint={action === "assign" ? t("startHint") : t("fromHint")}
          type="number"
          min={1}
          max={24}
          value={instalment}
          onChange={(event) => setInstalment(event.target.value)}
        />
      </div>
      {mode === "class" ? (
        <SelectField
          label={t("modes.class")}
          placeholder={t("choose")}
          options={classes}
          value={classId}
          onChange={(event) => setClassId(event.target.value)}
        />
      ) : null}
      {mode === "section" ? (
        <SelectField
          label={t("modes.section")}
          placeholder={t("choose")}
          options={sections.map((s) => ({ value: s.id, label: s.label }))}
          value={sectionId}
          onChange={(event) => setSectionId(event.target.value)}
        />
      ) : null}
      {mode === "students" ? (
        <StudentPicker
          picked={picked}
          onPick={(row) =>
            setPicked((rows) =>
              rows.some((r) => r.student_id === row.student_id) ? rows : [...rows, row],
            )
          }
          onRemove={(id) => setPicked((rows) => rows.filter((r) => r.student_id !== id))}
        />
      ) : null}
      {action === "assign" ? (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={replace}
            onChange={(event) => setReplace(event.target.checked)}
          />
          {t("replace")}
        </label>
      ) : null}
      {needsReason ? (
        <TextAreaField
          label={t("reason")}
          hint={action === "unassign" ? t("unassignHint") : t("replaceHint")}
          value={reason}
          minLength={REASON_MIN}
          maxLength={REASON_MAX}
          onChange={(event) => setReason(event.target.value)}
        />
      ) : null}
      <ApiErrorAlert error={run.error} />
      {result ? <ResultSummary result={result} /> : null}
      <div className="flex flex-wrap gap-3">
        <Button
          type="button"
          variant="secondary"
          disabled={!ready || run.isPending}
          onClick={() => run.mutate(true)}
        >
          {t("check")}
        </Button>
        <Button type="submit" loading={run.isPending} disabled={!ready}>
          {action === "assign" ? t("submitAssign") : t("submitUnassign")}
        </Button>
      </div>
    </form>
  );
}

function ResultSummary({ result }: { result: AddonAssignResult | AddonUnassignResult }) {
  const t = useTranslations("fees.addons.result");
  const prefix = result.dry_run ? t("checked") : t("done");
  if ("assigned" in result) {
    return (
      <Alert tone={result.dry_run ? "info" : "success"} live title={prefix}>
        {t("assigned", {
          assigned: result.assigned,
          charges: result.charges_posted,
          skipped: result.skipped.length,
          credits: result.credits_proposed,
        })}
      </Alert>
    );
  }
  return (
    <Alert tone={result.dry_run ? "info" : "success"} live title={prefix}>
      {t("unassigned", {
        ended: result.ended,
        credits: result.credits_proposed,
        notAssigned: result.not_assigned,
      })}{" "}
      <Amount value={result.credit_total} />
    </Alert>
  );
}

/** Pick students by name or admission number (the counter's fee-account search). */
function StudentPicker({
  picked,
  onPick,
  onRemove,
}: {
  picked: readonly AccountSummary[];
  onPick: (row: AccountSummary) => void;
  onRemove: (studentId: string) => void;
}) {
  const t = useTranslations("fees.addons.picker");
  const api = useBffClient("staff");
  const [text, setText] = useState("");
  const search = useApiMutation((query: string) =>
    unwrap(api.POST("/api/v1/fees/accounts/search", { body: { query } })),
  );
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <TextField
          label={t("search")}
          hint={t("searchHint")}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              if (text.trim().length >= 2) search.mutate(text.trim());
            }
          }}
        />
        <Button
          type="button"
          variant="secondary"
          disabled={text.trim().length < 2}
          onClick={() => search.mutate(text.trim())}
        >
          {t("find")}
        </Button>
      </div>
      <ApiErrorAlert error={search.error} />
      {search.data ? (
        <ul className="space-y-1" aria-label={t("results")}>
          {search.data.map((row) => (
            <li key={row.account_id} className="flex items-center gap-3 text-sm">
              <span>
                {row.display_name} · {row.admission_no} · {row.class_section}
              </span>
              <Button type="button" variant="ghost" size="sm" onClick={() => onPick(row)}>
                {t("add", { name: row.display_name ?? row.admission_no ?? "" })}
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      {picked.length ? (
        <ul className="flex flex-wrap gap-2" aria-label={t("picked")}>
          {picked.map((row) => (
            <li key={row.student_id}>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => onRemove(row.student_id)}
              >
                {t("remove", { name: row.display_name ?? row.admission_no ?? "" })}
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
