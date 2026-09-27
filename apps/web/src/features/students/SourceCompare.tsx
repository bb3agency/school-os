"use client";

import { useTranslations } from "next-intl";
import { useId, useRef, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Value } from "@/components/ui/Value";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatDate } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { LoadGate, SourceChip, useValueFormatter } from "./parts";
import { SensitiveValue } from "./SensitiveValue";
import type { Attribute, SourceValue, ValueSource } from "./types";

/**
 * "Values by source" (US-301, FR-STU-002, BR-01): the current value each source holds, side by
 * side, from GET /students/{id}/values, so the office sees where the register, Aadhaar (as
 * printed), UDISE+ and the board registration disagree before a portal submission. Read-only:
 * differences are fixed by recording a value or, for identity fields, a correction request.
 * Restricted values stay masked (only "Show" reveals them, audited); Aadhaar is never more
 * than its last 4 digits (invariant 4).
 */

const MAIN_SOURCES = [
  "admission_register",
  "aadhaar_as_printed",
  "udise_plus",
  "board_registration",
] as const satisfies readonly ValueSource[];
const REGISTER = "admission_register";

export const valuesKey = (studentId: string) => ["staff", "students", studentId, "values"] as const;

/** Same text for comparison: Unicode NFC, trimmed, single spaces (case is kept: it matters). */
export function sameValue(a: string, b: string): boolean {
  const norm = (value: string) => value.normalize("NFC").trim().replace(/\s+/g, " ");
  return norm(a) === norm(b);
}

interface Index {
  sorted: readonly Attribute[];
  byKey: ReadonlyMap<string, Attribute>;
  label: (key: string) => string;
}

function differs(value: SourceValue, register: SourceValue | undefined): boolean {
  if (!register || value.id === register.id) return false;
  if (value.masked || register.masked || value.value === null || register.value === null) {
    return false;
  }
  return !sameValue(value.value, register.value);
}

function ShownValue({
  value,
  attribute,
  label,
  studentId,
  canReveal,
}: {
  value: SourceValue;
  attribute: Attribute | undefined;
  label: string;
  studentId: string;
  canReveal: boolean;
}) {
  const format = useValueFormatter();
  if (value.masked) {
    return (
      <SensitiveValue
        studentId={studentId}
        attributeKey={value.attribute_key}
        fieldLabel={label}
        valueId={value.id}
        canReveal={canReveal}
      />
    );
  }
  return <Value>{format(value.value, attribute)}</Value>;
}

/** Every value ever recorded for one field (newest first), replaced ones marked. */
function HistoryDialog({
  label,
  values,
  attribute,
  studentId,
  canReveal,
}: {
  label: string;
  values: readonly SourceValue[];
  attribute: Attribute | undefined;
  studentId: string;
  canReveal: boolean;
}) {
  const t = useTranslations("students.bySource");
  const ts = useTranslations("students");
  const tc = useTranslations("common");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const [open, setOpen] = useState(false);
  const close = () => dialogRef.current?.close();

  return (
    <>
      <Button
        ref={triggerRef}
        size="sm"
        variant="ghost"
        aria-haspopup="dialog"
        onClick={() => {
          setOpen(true);
          dialogRef.current?.showModal();
        }}
      >
        {t("history")}
        <span className="sr-only">: {label}</span>
      </Button>
      <dialog
        ref={dialogRef}
        aria-labelledby={titleId}
        onClose={() => {
          setOpen(false);
          triggerRef.current?.focus();
        }}
        className="m-auto w-[min(40rem,calc(100vw-2rem))] rounded-lg border border-border bg-surface p-0 text-ink shadow-xl"
      >
        {open ? (
          <>
            <div className="flex items-start justify-between gap-4 border-b border-border p-5">
              <h2 id={titleId} className="text-lg font-semibold">
                {t("historyTitle", { field: label })}
              </h2>
              <button
                type="button"
                onClick={close}
                aria-label={tc("close")}
                className="rounded-md p-1 text-ink-muted hover:bg-surface-muted hover:text-ink"
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  className="size-5"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            </div>
            <ol className="max-h-[60vh] space-y-3 overflow-y-auto p-5 text-sm">
              {values.map((value) => (
                <li key={value.id} className="space-y-1 border-b border-border pb-3 last:border-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <SourceChip source={value.source} verification={value.verification_status} />
                    <ShownValue
                      value={value}
                      attribute={attribute}
                      label={label}
                      studentId={studentId}
                      canReveal={canReveal}
                    />
                    {value.current ? null : <Badge>{t("replaced")}</Badge>}
                  </div>
                  <p className="text-xs text-ink-muted">
                    {ts("detail.recordedOn", { date: formatDate(value.recorded_at) ?? "" })}
                    {value.change_request_id ? ` · ${t("fromChangeRequest")}` : ""}
                  </p>
                </li>
              ))}
            </ol>
            <div className="flex justify-end border-t border-border p-5">
              <Button variant="secondary" onClick={close}>
                {tc("close")}
              </Button>
            </div>
          </>
        ) : null}
      </dialog>
    </>
  );
}

export interface ValuesBySourceProps {
  studentId: string;
  index: Index;
  canReveal: boolean;
}

export function ValuesBySourceView({
  studentId,
  index,
  canReveal,
  values,
}: ValuesBySourceProps & { values: Loadable<readonly SourceValue[]> }) {
  const t = useTranslations("students.bySource");
  const ts = useTranslations("students");

  if (values.status !== "ready") {
    return (
      <Card title={t("title")} description={t("description")}>
        <LoadGate state={values} />
      </Card>
    );
  }

  const byKey = new Map<string, SourceValue[]>();
  for (const value of values.data) {
    const list = byKey.get(value.attribute_key) ?? [];
    list.push(value);
    byKey.set(value.attribute_key, list);
  }
  const keys = [
    ...index.sorted.map((item) => item.key).filter((key) => byKey.has(key)),
    ...[...byKey.keys()].filter((key) => !index.byKey.has(key)),
  ];
  const isMain = (source: string) => (MAIN_SOURCES as readonly string[]).includes(source);

  return (
    <Card title={t("title")} description={t("description")}>
      {keys.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("empty")}</p>
      ) : (
        <div
          role="region"
          aria-label={t("table")}
          tabIndex={0}
          className="overflow-x-auto rounded-md border border-border print:overflow-visible print:border-0"
        >
          <table className="w-full border-collapse text-left text-sm">
            <caption className="sr-only">{t("table")}</caption>
            <thead className="bg-surface-muted">
              <tr>
                <th scope="col" className="px-3 py-2 font-semibold">
                  {ts("detail.colField")}
                </th>
                {MAIN_SOURCES.map((source) => (
                  <th key={source} scope="col" className="px-3 py-2 font-semibold">
                    {ts(`sources.${source}`)}
                  </th>
                ))}
                <th scope="col" className="px-3 py-2 font-semibold">
                  {t("otherSources")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {keys.map((key) => {
                const all = byKey.get(key) ?? [];
                const current = all.filter((value) => value.current);
                const register = current.find((value) => value.source === REGISTER);
                const attribute = index.byKey.get(key);
                const label = index.label(key);
                const cell = (value: SourceValue, withChip: boolean) => (
                  <div key={value.id} className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {withChip ? (
                        <SourceChip
                          source={value.source}
                          verification={value.verification_status}
                        />
                      ) : null}
                      <span className="font-semibold">
                        <ShownValue
                          value={value}
                          attribute={attribute}
                          label={label}
                          studentId={studentId}
                          canReveal={canReveal}
                        />
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {withChip ? null : (
                        <span className="text-xs text-ink-muted">
                          {ts(`verification.${value.verification_status}`)}
                        </span>
                      )}
                      {differs(value, register) ? (
                        <Badge tone="warning">{t("differs")}</Badge>
                      ) : null}
                    </div>
                  </div>
                );
                return (
                  <tr key={key} className="align-top">
                    <th scope="row" className="px-3 py-3 text-left font-semibold whitespace-normal">
                      <span className="block">{label}</span>
                      <span data-print="hide">
                        <HistoryDialog
                          label={label}
                          values={all}
                          attribute={attribute}
                          studentId={studentId}
                          canReveal={canReveal}
                        />
                      </span>
                    </th>
                    {MAIN_SOURCES.map((source) => {
                      const found = current.filter((value) => value.source === source);
                      return (
                        <td key={source} className="px-3 py-3">
                          {found.length === 0 ? (
                            <Value>{null}</Value>
                          ) : (
                            found.map((value) => cell(value, false))
                          )}
                        </td>
                      );
                    })}
                    <td className="px-3 py-3">
                      {current.filter((value) => !isMain(value.source)).length === 0 ? (
                        <Value>{null}</Value>
                      ) : (
                        <div className="space-y-2">
                          {current
                            .filter((value) => !isMain(value.source))
                            .map((value) => cell(value, true))}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/** GET /students/{id}/values (full history; 404 outside the caller's scope). */
export function ValuesBySource(props: ValuesBySourceProps) {
  const api = useBffClient("staff");
  const values = useApiQuery(valuesKey(props.studentId), () =>
    unwrap(
      api.GET("/api/v1/students/{student_id}/values", {
        params: { path: { student_id: props.studentId } },
      }),
    ),
  );
  return <ValuesBySourceView {...props} values={values} />;
}
