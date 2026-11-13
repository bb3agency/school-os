"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { useDocumentList } from "@/features/documents/data";
import { Link } from "@/i18n/navigation";
import { toLoadable, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import * as z from "@/lib/zod";
import {
  COMPLIANCE_MANAGE,
  COMPLIANCE_READ,
  SAFETY_KEYS,
  checkFormSchema,
  fetchChecks,
  recordCheck,
  removeCheck,
  type CheckEntry,
  type CheckState,
  type CheckStatus,
  type ChecksOverview,
} from "./safety-data";

const STATE_VARIANTS: Record<CheckState, PillVariant> = {
  overdue: "negative",
  never: "review",
  due_soon: "progress",
  ok: "positive",
  logged: "tag",
};

function CheckStatePill({ state }: { state: CheckState }) {
  const t = useTranslations("complianceChecks.state");
  return (
    <Pill variant={STATE_VARIANTS[state]} icon={state === "overdue" ? "alert" : null}>
      {t(state)}
    </Pill>
  );
}

/** The "log a check" dialog (FR-COMP-048): counts and a short note, never names. */
function LogDialog({
  overview,
  kind: initialKind,
  onClose,
}: {
  overview: ChecksOverview;
  kind: string;
  onClose: () => void;
}) {
  const t = useTranslations("complianceChecks.form");
  const tc = useTranslations("complianceChecks");
  const api = useBffClient("staff");
  const [kind, setKind] = useState(initialKind);
  const docs = useDocumentList({ purpose: null, docType: null, status: "active" }, undefined, true);
  const info = overview.kinds.find((k) => k.kind === kind);
  const drill = info?.drill === true || kind === "other";
  const docOptions =
    docs.status === "ready" ? docs.data.data.map((d) => ({ value: d.id, label: d.title })) : [];
  return (
    <ActionDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t("title")}
      description={t("description")}
      confirmLabel={t("record")}
      schema={checkFormSchema}
      invalidate={[SAFETY_KEYS.checks]}
      errorNamespace="complianceChecks"
      errorSummary
      submit={(data, key) => recordCheck(api, data, info?.drill === true, key)}
      onSuccess={onClose}
    >
      {(errors) => (
        <div className="space-y-4">
          <Alert tone="info">{tc("privacyNote")}</Alert>
          <SelectField
            label={t("kind")}
            name="kind"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            placeholder={t("choose")}
            options={overview.kinds
              .filter((k) => k.applies || k.kind === "other")
              .map((k) => ({ value: k.kind, label: k.label }))}
            error={errors.kind}
            required
          />
          {info ? <p className="text-sm text-ink-muted">{info.note}</p> : null}
          {kind === "other" ? (
            <TextField
              label={t("label")}
              name="label"
              maxLength={120}
              minLength={3}
              error={errors.label}
              required
            />
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label={t("doneOn")}
              name="done_on"
              type="date"
              max={overview.today}
              defaultValue={overview.today}
              error={errors.done_on}
              required
            />
            <SelectField
              label={t("ledBy")}
              name="led_by_membership_id"
              defaultValue=""
              placeholder={t("nobody")}
              options={(overview.leaders ?? []).map((m) => ({
                value: m.membership_id,
                label: m.display_name ?? m.membership_id,
              }))}
              error={errors.led_by_membership_id}
            />
            {drill ? (
              <>
                <TextField
                  label={t("participants")}
                  name="participants_count"
                  inputMode="numeric"
                  autoComplete="off"
                  error={errors.participants_count}
                />
                <TextField
                  label={t("duration")}
                  hint={t("durationHint")}
                  name="duration_minutes"
                  inputMode="numeric"
                  autoComplete="off"
                  error={errors.duration_minutes}
                />
              </>
            ) : null}
          </div>
          <SelectField
            label={t("result")}
            name="result"
            defaultValue="ok"
            options={[
              { value: "ok", label: tc("result.ok") },
              { value: "action_needed", label: tc("result.action_needed") },
            ]}
            error={errors.result}
          />
          <TextAreaField
            label={t("observations")}
            hint={t("observationsHint")}
            name="observations"
            maxLength={500}
            rows={3}
            error={errors.observations}
          />
          <SelectField
            label={t("document")}
            hint={t("documentHint")}
            name="document_id"
            defaultValue=""
            placeholder={docs.status === "ready" ? t("noDocument") : t("loading")}
            options={docOptions}
            error={errors.document_id}
          />
        </div>
      )}
    </ActionDialog>
  );
}

function DueCell({ row }: { row: CheckStatus }) {
  const t = useTranslations("complianceChecks");
  return (
    <span className="flex flex-col items-start gap-1">
      <CheckStatePill state={row.state} />
      {row.next_due ? (
        <span className="num text-sm whitespace-nowrap">
          {t("dueOn", { date: formatDate(row.next_due) ?? "" })}
          {row.days_left !== null && row.days_left >= 0 && row.state !== "ok"
            ? ` · ${t("daysLeft", { days: row.days_left })}`
            : null}
          {row.days_left !== null && row.days_left < 0
            ? ` · ${t("daysAgo", { days: -row.days_left })}`
            : null}
        </span>
      ) : null}
    </span>
  );
}

/**
 * Safety drills and periodic checks (FC-09; FR-COMP-046..FR-COMP-055): every check that applies
 * to the school with when it was last done and when the next is due, most urgent first, and the
 * latest entries. The office logs each drill or check; reminders go out before it falls due.
 * Counts and short notes only, never student names.
 */
export function ChecksScreen() {
  const t = useTranslations("complianceChecks");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const tco = useTranslations("compliance");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(COMPLIANCE_READ);
  const manage = can(COMPLIANCE_MANAGE);
  const [logging, setLogging] = useState<string | null>(null);
  const [removing, setRemoving] = useState<CheckEntry | null>(null);
  const overview = useQuery({
    queryKey: SAFETY_KEYS.checks,
    queryFn: () => fetchChecks(api),
    enabled: allowed,
    retry: false,
  });
  const data = overview.data;

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[
        { label: tn("home"), href: "/" },
        { label: tco("title"), href: "/compliance" },
        { label: t("title") },
      ]}
      actions={
        manage && data ? (
          <Button variant="primary" onClick={() => setLogging("")}>
            {t("logButton")}
          </Button>
        ) : undefined
      }
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

  const columns: ColumnV2<CheckStatus>[] = [
    {
      key: "check",
      stack: "title",
      header: t("colCheck"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold break-words text-ink">{row.kind_label}</span>
          <span className="text-xs text-ink-muted">
            {row.every_months ? t("every", { months: row.every_months }) : t("noFrequency")}
          </span>
        </span>
      ),
    },
    { key: "status", header: t("colStatus"), cell: (row) => <DueCell row={row} /> },
    {
      key: "last",
      header: t("colLastDone"),
      cell: (row) =>
        row.last_done_on ? (
          <span className="num text-sm whitespace-nowrap">{formatDate(row.last_done_on)}</span>
        ) : (
          <span className="text-sm text-ink-muted">{t("never")}</span>
        ),
    },
  ];
  if (manage) {
    columns.push({
      key: "actions",
      header: t("colActions"),
      cell: (row) => (
        <Button
          size="sm"
          variant={row.state === "overdue" || row.state === "due_soon" ? "primary" : "secondary"}
          onClick={() => setLogging(row.kind)}
          aria-label={t("logAria", { name: row.kind_label })}
        >
          {t("logButton")}
        </Button>
      ),
    });
  }

  const recentColumns: ColumnV2<CheckEntry>[] = [
    {
      key: "date",
      header: t("colDate"),
      cell: (e) => <span className="num text-sm whitespace-nowrap">{formatDate(e.done_on)}</span>,
    },
    {
      key: "check",
      stack: "title",
      header: t("colCheck"),
      cell: (e) => <span className="font-semibold break-words text-ink">{e.kind_label}</span>,
    },
    {
      key: "people",
      header: t("colPeople"),
      cell: (e) => (
        <span className="num text-sm">
          {[
            e.participants_count !== null ? t("people", { count: e.participants_count }) : null,
            e.duration_minutes !== null ? t("minutes", { minutes: e.duration_minutes }) : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      ),
    },
    {
      key: "result",
      header: t("colResult"),
      cell: (e) => (
        <Pill variant={e.result === "action_needed" ? "negative" : "positive"}>
          {t(`result.${e.result}`)}
        </Pill>
      ),
    },
    {
      key: "notes",
      header: t("colNotes"),
      cell: (e) => <span className="text-sm break-words">{e.observations}</span>,
    },
    {
      key: "report",
      header: t("colReport"),
      cell: (e) =>
        e.document ? (
          <Link
            href={`/documents/${e.document.id}`}
            className="text-sm break-anywhere text-primary underline-offset-4 hover:underline"
          >
            {e.document.title ?? t("reportHidden")}
          </Link>
        ) : null,
    },
  ];
  if (manage) {
    recentColumns.push({
      key: "actions",
      header: t("colActions"),
      cell: (e) => (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setRemoving(e)}
          aria-label={t("removeAria", { name: e.kind_label, date: formatDate(e.done_on) ?? "" })}
        >
          {t("remove")}
        </Button>
      ),
    });
  }

  return (
    <div className="space-y-5">
      {header}
      {data ? (
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <StatCard
            label={t("counts.overdue")}
            value={String(data.counts.overdue)}
            unavailableLabel=""
          />
          <StatCard
            label={t("counts.never")}
            value={String(data.counts.never)}
            unavailableLabel=""
          />
          <StatCard
            label={t("counts.dueSoon", { days: data.due_soon_days })}
            value={String(data.counts.due_soon)}
            unavailableLabel=""
          />
        </dl>
      ) : null}
      {data && !data.verified ? (
        <Alert tone="info" title={t("unverifiedTitle")}>
          {t("unverifiedBody")}
        </Alert>
      ) : null}
      <DataTableV2
        id="compliance-checks"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...overview, data: data?.statuses })}
        rowKey={(row) => row.kind}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
        onRetry={() => void overview.refetch()}
      />
      <h2 className="text-lg font-semibold text-ink">{t("recentTitle")}</h2>
      <DataTableV2
        id="compliance-checks-recent"
        stacked
        caption={t("recentTitle")}
        captionHidden
        columns={recentColumns}
        state={toLoadable({ ...overview, data: data?.recent })}
        rowKey={(e) => e.id}
        emptyTitle={t("recentEmptyTitle")}
        emptyBody={t("recentEmptyBody")}
        onRetry={() => void overview.refetch()}
      />
      {logging !== null && data ? (
        <LogDialog overview={data} kind={logging} onClose={() => setLogging(null)} />
      ) : null}
      <ActionDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        title={t("removeDialog.title")}
        description={removing ? `${removing.kind_label} · ${formatDate(removing.done_on)}` : ""}
        confirmLabel={t("removeDialog.confirm")}
        confirmVariant="danger"
        consequence={t("removeDialog.consequence")}
        schema={z.object({})}
        invalidate={[SAFETY_KEYS.checks]}
        errorNamespace="complianceChecks"
        submit={() => {
          if (!removing) return Promise.reject(new Error("no entry"));
          return removeCheck(api, removing);
        }}
      />
    </div>
  );
}
