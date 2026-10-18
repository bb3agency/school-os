"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useMemo, useRef, useState, type FormEvent } from "react";
import * as z from "@/lib/zod";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Label } from "@/components/ui/Label";
import { Menu } from "@/components/ui/Menu";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Tabs } from "@/components/ui/Tabs";
import {
  UploadError,
  uploadDocument,
  extensionOf,
  type UploadProgress,
} from "@/features/imports/upload";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useApiMutation, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatDateTime, formatInr } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import { isFeesOff, toPaise } from "./data";
import { Amount, FeesGate, NoAccess } from "./parts";
import {
  confirmable,
  IGNORE_REASONS,
  initialSelection,
  RECONCILE,
  STATEMENT_MAX_BYTES,
  UPI_KEYS,
  type StatementImport,
  type StatementLine,
  type UpiExceptions,
} from "./upi";

const STATUS_PILL: Record<string, PillVariant> = {
  unmatched: "review",
  proposed: "progress",
  confirmed: "done",
  ignored: "sample",
};

function LineStatus({ line }: { line: StatementLine }) {
  const t = useTranslations("fees.reconcile.status");
  return <Pill variant={STATUS_PILL[line.match_status] ?? "tag"}>{t(line.match_status)}</Pill>;
}

/** Choose the receipt a credit pays (issued UPI receipts of the same amount) and confirm. */
function MatchDialog({ line }: { line: StatementLine }) {
  const t = useTranslations("fees.reconcile");
  const api = useBffClient("staff");
  const receipts = useApiQuery(["staff", "fees", "upi", "candidates", line.amount], () =>
    unwrap(
      api.GET("/api/v1/fees/receipts", {
        params: { query: { mode: "upi_qr", status: "issued", limit: 200 } },
      }),
    ),
  );
  const paise = toPaise(line.amount);
  const options =
    receipts.status === "ready"
      ? receipts.data.data
          .filter((r) => toPaise(r.amount) === paise && r.id !== line.receipt?.id)
          .map((r) => ({
            value: r.id,
            label: `${r.receipt_number} · ${formatDate(r.receipt_date) ?? ""} · ${r.student_name} · ${r.class_section}`,
          }))
      : [];
  return (
    <ActionDialog
      triggerLabel={t("match")}
      triggerSize="sm"
      triggerDescription={t("lineLabel", { row: line.line_no })}
      title={t("matchTitle")}
      description={t("matchBody")}
      consequence={t("matchConsequence")}
      confirmLabel={t("matchConfirm")}
      schema={z.object({ receipt_id: z.string().uuid({ error: "required" }) })}
      invalidate={[UPI_KEYS.statements, ["staff", "fees", "upi"], ["staff", "fees", "receipt"]]}
      errorNamespace="fees"
      submit={(input) =>
        unwrap(
          api.POST("/api/v1/fees/bank-statement-lines/{line_id}/confirm", {
            params: { path: { line_id: line.id } },
            body: { receipt_id: input.receipt_id },
          }),
        )
      }
    >
      {(errors) =>
        options.length === 0 ? (
          <p>{receipts.status === "loading" ? t("loadingReceipts") : t("noCandidates")}</p>
        ) : (
          <SelectField
            name="receipt_id"
            label={t("receipt")}
            error={errors.receipt_id}
            options={options}
            placeholder={t("chooseReceipt")}
          />
        )
      }
    </ActionDialog>
  );
}

function DismissDialog({ line }: { line: StatementLine }) {
  const t = useTranslations("fees.reconcile");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("dismiss")}
      triggerSize="sm"
      triggerVariant="ghost"
      triggerDescription={t("lineLabel", { row: line.line_no })}
      title={t("dismissTitle")}
      description={t("dismissBody")}
      consequence={t("dismissConsequence")}
      confirmLabel={t("dismiss")}
      schema={z.object({
        reason: z.enum(["not_a_fee", "recorded_elsewhere", "duplicate_credit", "other"]),
      })}
      invalidate={[["staff", "fees", "upi"]]}
      errorNamespace="fees"
      submit={(input) =>
        unwrap(
          api.POST("/api/v1/fees/bank-statement-lines/{line_id}/dismiss", {
            params: { path: { line_id: line.id } },
            body: { reason: input.reason },
          }),
        )
      }
    >
      {() => (
        <SelectField
          name="reason"
          label={t("dismissReason")}
          options={IGNORE_REASONS.map((r) => ({ value: r, label: t(`ignoreReasons.${r}`) }))}
        />
      )}
    </ActionDialog>
  );
}

/** Statement lines: tick proposals, see why a credit needs a person, match or dismiss it. */
function LinesTable({
  lines,
  selected,
  onToggle,
  caption,
}: {
  lines: readonly StatementLine[];
  selected?: Set<string>;
  onToggle?: (id: string) => void;
  caption: string;
}) {
  const t = useTranslations("fees.reconcile");
  return (
    <TableScroll label={caption} framed>
      <Table>
        <THead>
          <Tr>
            {onToggle ? (
              <Th className="w-10">
                <span className="sr-only">{t("select")}</span>
              </Th>
            ) : null}
            <Th>{t("valueDate")}</Th>
            <Th numeric>{t("amountInr")}</Th>
            <Th>{t("reference")}</Th>
            <Th>{t("narration")}</Th>
            <Th>{t("statusColumn")}</Th>
            <Th>{t("receipt")}</Th>
            <Th>{t("actions")}</Th>
          </Tr>
        </THead>
        <TBody>
          {lines.map((line) => {
            const open = line.match_status === "unmatched" || line.match_status === "proposed";
            const rematch = line.exception === "confirmed_receipt_cancelled";
            return (
              <Tr key={line.id}>
                {onToggle ? (
                  <Td>
                    {line.match_status === "proposed" ? (
                      <input
                        type="checkbox"
                        className="size-5"
                        aria-label={t("selectLine", { row: line.line_no })}
                        checked={selected?.has(line.id) ?? false}
                        onChange={() => onToggle(line.id)}
                      />
                    ) : null}
                  </Td>
                ) : null}
                <Td>
                  <span className="num whitespace-nowrap">{formatDate(line.value_date)}</span>
                </Td>
                <Td numeric>
                  <Amount value={line.amount} />
                </Td>
                <Td className="num font-mono whitespace-nowrap">
                  {line.utr_or_ref ?? "—"}
                  {line.ref_kind === "utr" ? <span className="sr-only"> ({t("utr")})</span> : null}
                </Td>
                <Td className="max-w-64 break-words text-sm text-ink-muted">
                  {line.narration_erased ? t("narrationErased") : (line.narration ?? "—")}
                </Td>
                <Td>
                  <LineStatus line={line} />
                  {line.match_kind ? (
                    <span className="block text-xs text-ink-muted">
                      {t(`kinds.${line.match_kind}`)}
                    </span>
                  ) : null}
                  {line.exception ? (
                    <span className="block text-sm text-warning-ink">
                      {t(`exceptions.${line.exception}`)}
                    </span>
                  ) : null}
                  {line.ignore_reason ? (
                    <span className="block text-xs text-ink-muted">
                      {t(`ignoreReasons.${line.ignore_reason}`)}
                    </span>
                  ) : null}
                </Td>
                <Td>
                  {(line.receipt ?? line.conflict_receipt) ? (
                    <ReceiptRef
                      receipt={(line.receipt ?? line.conflict_receipt)!}
                      conflict={!line.receipt}
                    />
                  ) : (
                    "—"
                  )}
                </Td>
                <Td>
                  <div className="flex flex-wrap gap-2">
                    {open || rematch ? <MatchDialog line={line} /> : null}
                    {open ? <DismissDialog line={line} /> : null}
                  </div>
                </Td>
              </Tr>
            );
          })}
        </TBody>
      </Table>
    </TableScroll>
  );
}

function ReceiptRef({
  receipt,
  conflict,
}: {
  receipt: NonNullable<StatementLine["receipt"]>;
  conflict: boolean;
}) {
  const t = useTranslations("fees.reconcile");
  return (
    <span className="block text-sm">
      <Link
        href={`/fees/receipts/${receipt.id}`}
        className="num font-mono font-semibold whitespace-nowrap text-primary underline underline-offset-4"
      >
        {receipt.receipt_number}
      </Link>
      <span className="block">
        {receipt.student_name} · {receipt.class_section}
      </span>
      <span className="num block whitespace-nowrap text-ink-muted">
        <Amount value={receipt.amount} /> · {formatDate(receipt.receipt_date)}
      </span>
      {conflict ? <span className="block text-warning-ink">{t("notCorrected")}</span> : null}
    </span>
  );
}

function StatementReview({ id }: { id: string }) {
  const api = useBffClient("staff");
  const detail = useApiQuery(UPI_KEYS.statement(id), () =>
    unwrap(
      api.GET("/api/v1/fees/bank-statements/{statement_id}", {
        params: { path: { statement_id: id } },
      }),
    ),
  );
  return (
    <FeesGate data={detail}>
      {(d) => <ReviewBody key={id} id={id} lines={d.lines} statement={d.statement} />}
    </FeesGate>
  );
}

function ReviewBody({
  id,
  lines,
  statement,
}: {
  id: string;
  lines: StatementLine[];
  statement: StatementImport;
}) {
  const t = useTranslations("fees.reconcile");
  const api = useBffClient("staff");
  const proposals = useMemo(() => confirmable(lines), [lines]);
  const [selected, setSelected] = useState<Set<string>>(() => initialSelection(lines));
  const [result, setResult] = useState<{ confirmed: number; skipped: number } | null>(null);
  const toggle = (lineId: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(lineId)) next.delete(lineId);
      else next.add(lineId);
      return next;
    });
  const count = proposals.filter((l) => selected.has(l.id)).length;
  return (
    <Card
      title={t("reviewTitle", {
        from: formatDate(statement.period_from) ?? "—",
        to: formatDate(statement.period_to) ?? "—",
      })}
      description={t("reviewBody", {
        credits: statement.credit_count,
        duplicates: statement.duplicate_count,
      })}
      actions={
        <ActionDialog
          triggerLabel={t("confirmN", { count })}
          triggerVariant="primary"
          triggerDisabled={count === 0}
          title={t("confirmTitle", { count })}
          description={t("confirmBody")}
          consequence={t("confirmConsequence")}
          confirmLabel={t("confirmN", { count })}
          schema={z.object({})}
          invalidate={[
            ["staff", "fees", "upi"],
            ["staff", "fees", "receipt"],
          ]}
          errorNamespace="fees"
          onSuccess={(out) => setResult({ confirmed: out.confirmed, skipped: out.skipped.length })}
          submit={() =>
            unwrap(
              api.POST("/api/v1/fees/bank-statements/{statement_id}/confirm", {
                params: { path: { statement_id: id } },
                body: { line_ids: proposals.filter((l) => selected.has(l.id)).map((l) => l.id) },
              }),
            )
          }
        />
      }
    >
      <div className="space-y-4">
        {result ? (
          <Alert tone={result.skipped ? "warning" : "success"} live>
            {t("confirmed", { count: result.confirmed, skipped: result.skipped })}
          </Alert>
        ) : null}
        {lines.length === 0 ? (
          <p>{t("noCredits")}</p>
        ) : (
          <LinesTable
            lines={lines}
            selected={selected}
            onToggle={toggle}
            caption={t("linesCaption")}
          />
        )}
      </div>
    </Card>
  );
}

function StatementUpload({ onImported }: { onImported: (id: string) => void }) {
  const t = useTranslations("fees.reconcile");
  const ts = useTranslations("insights.sheet");
  const api = useBffClient("staff");
  const me = useStaffMe();
  const fileRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const presets = useApiQuery(UPI_KEYS.presets, () =>
    unwrap(api.GET("/api/v1/fees/bank-statement-presets")),
  );
  const [preset, setPreset] = useState("");
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const importStatement = useApiMutation(
    (documentId: string) =>
      unwrap(
        api.POST("/api/v1/fees/bank-statements", {
          body: { document_id: documentId, ...(preset ? { preset } : {}) },
        }),
      ),
    { invalidate: [["staff", "fees", "upi"]] },
  );

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = fileRef.current?.files?.[0];
    setProblem(null);
    setError(undefined);
    if (!file) return setProblem("fileRequired");
    if (!["xlsx", "csv"].includes(extensionOf(file.name))) return setProblem("fileType");
    if (file.size > STATEMENT_MAX_BYTES) return setProblem("fileTooLarge");
    try {
      const documentId = await uploadDocument(api, file, "import_file", {
        onProgress: setProgress,
        // Only the person who uploads a statement may read it (the API deletes it once read).
        acl: me ? [{ principal_type: "membership", principal_ref: me.membership_id }] : [],
      });
      setProgress(null);
      const out = await importStatement.mutateAsync(documentId);
      onImported(out.id);
    } catch (failure) {
      if (failure instanceof UploadError) setProblem(`upload.${failure.code}`);
      else setError(failure);
    } finally {
      setProgress(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <Card title={t("uploadTitle")} description={t("uploadBody")}>
      <form onSubmit={(e) => void onSubmit(e)} noValidate className="space-y-3">
        <Alert tone="info">{t("deletedNote")}</Alert>
        <SelectField
          name="preset"
          label={t("bank")}
          hint={t("bankHint")}
          value={preset}
          onChange={(e) => setPreset(e.target.value)}
          options={[
            { value: "", label: t("detect") },
            ...(presets.status === "ready"
              ? presets.data.map((p) => ({ value: p.preset, label: p.name }))
              : []),
          ]}
        />
        <div className="space-y-1">
          <Label htmlFor={inputId}>{t("file")}</Label>
          <input
            id={inputId}
            ref={fileRef}
            type="file"
            accept=".xlsx,.csv"
            className="block w-full text-sm text-ink file:mr-3 file:min-h-10 file:rounded-md file:border-0 file:bg-surface-muted file:px-3 file:text-ink"
          />
        </div>
        <Button type="submit" disabled={progress !== null || importStatement.isPending}>
          {t("read")}
        </Button>
        {progress ? (
          <p className="text-sm text-ink-muted" role="status">
            {ts(`stage.${progress.stage}`)}
          </p>
        ) : importStatement.isPending ? (
          <p className="text-sm text-ink-muted" role="status">
            {t("reading")}
          </p>
        ) : null}
      </form>
      {problem ? (
        <Alert tone="danger" live className="mt-3">
          {translateOr(ts, `fileProblem.${problem}`, "fileProblem.fileType")}
        </Alert>
      ) : null}
      <ApiErrorAlert error={error} namespace="fees" className="mt-3" />
    </Card>
  );
}

function BankTab() {
  const t = useTranslations("fees.reconcile");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const list = useApiQuery(UPI_KEYS.statements, () =>
    unwrap(api.GET("/api/v1/fees/bank-statements", { params: { query: { limit: 25 } } })),
  );
  const [open, setOpen] = useState<string | null>(null);
  const columns: ColumnV2<StatementImport>[] = [
    {
      key: "importedAt",
      name: t("importedAt"),
      header: t("importedAt"),
      hideable: false,
      cell: (s) => <span className="num whitespace-nowrap">{formatDateTime(s.imported_at)}</span>,
    },
    {
      key: "period",
      name: t("period"),
      header: t("period"),
      cell: (s) => (
        <span className="num whitespace-nowrap">
          {formatDate(s.period_from) ?? "—"} – {formatDate(s.period_to) ?? "—"}
        </span>
      ),
    },
    {
      key: "credits",
      name: t("credits"),
      header: t("credits"),
      numeric: true,
      cell: (s) => formatCount(s.credit_count, locale),
    },
    {
      key: "proposed",
      name: t("proposed"),
      header: t("proposed"),
      numeric: true,
      cell: (s) => formatCount(s.proposed_count, locale),
    },
    {
      key: "importedBy",
      name: t("importedBy"),
      header: t("importedBy"),
      cell: (s) => s.imported_by_name ?? "—",
    },
    {
      key: "actions",
      name: t("actions"),
      header: <span className="sr-only">{t("actions")}</span>,
      hideable: false,
      stack: "actions",
      cell: (s) => (
        <Button
          size="sm"
          variant={open === s.id ? "primary" : "secondary"}
          aria-pressed={open === s.id}
          onClick={() => setOpen(s.id)}
        >
          {t("review")}
        </Button>
      ),
    },
  ];
  return (
    <div className="space-y-4">
      <StatementUpload onImported={setOpen} />
      {isFeesOff(list) ? (
        <FeesGate data={list}>{() => null}</FeesGate>
      ) : (
        <DataTableV2
          id="fees-bank-statements"
          caption={t("statementsTitle")}
          columns={columns}
          state={list}
          rowKey={(s) => s.id}
          emptyTitle={t("noStatements")}
          emptyBody={t("noStatementsBody")}
          onRetry={() => void queryClient.invalidateQueries({ queryKey: UPI_KEYS.statements })}
          stacked
        />
      )}
      {open ? <StatementReview id={open} /> : null}
    </div>
  );
}

type UnconfirmedReceipt = UpiExceptions["unconfirmed_receipts"][number];

function ExceptionsTab() {
  const t = useTranslations("fees.reconcile");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const exceptions = useApiQuery(UPI_KEYS.exceptions, () =>
    unwrap(api.GET("/api/v1/fees/upi-exceptions")),
  );
  const report = useApiQuery(UPI_KEYS.report, () => unwrap(api.GET("/api/v1/fees/reports/upi")));
  const rematch = useApiMutation(() => unwrap(api.POST("/api/v1/fees/bank-statements/rematch")), {
    invalidate: [["staff", "fees", "upi"]],
  });
  const locale = useLocale() as Locale;
  const unconfirmedId = useId();
  const unconfirmedColumns: ColumnV2<UnconfirmedReceipt>[] = [
    {
      key: "receipt",
      name: t("receipt"),
      header: t("receipt"),
      hideable: false,
      cell: (r) => (
        <Link
          href={`/fees/receipts/${r.id}`}
          className="num font-mono font-semibold whitespace-nowrap text-primary underline underline-offset-4"
        >
          {r.receipt_number}
        </Link>
      ),
    },
    {
      key: "receiptDate",
      name: t("receiptDate"),
      header: t("receiptDate"),
      cell: (r) => <span className="num whitespace-nowrap">{formatDate(r.receipt_date)}</span>,
    },
    {
      key: "amount",
      name: t("amount"),
      header: t("amountInr"),
      numeric: true,
      cell: (r) => <Amount value={r.amount} />,
    },
    {
      key: "utr",
      name: t("utr"),
      header: t("utr"),
      cell: (r) => <span className="num font-mono whitespace-nowrap">{r.utr ?? "—"}</span>,
    },
    {
      key: "student",
      name: t("student"),
      header: t("student"),
      cell: (r) => (
        <>
          {r.student_name} · {r.class_section}
        </>
      ),
    },
    {
      key: "daysWaiting",
      name: t("daysWaiting"),
      header: t("daysWaiting"),
      numeric: true,
      cell: (r) => formatCount(r.days_waiting, locale),
    },
  ];
  return (
    <div className="space-y-4">
      <FeesGate data={report}>
        {(r) => (
          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label={t("reportConfirmed")}
              value={formatCount(r.confirmed_count, locale)}
              unavailableLabel={tc("notAvailable")}
              hint={<span className="num">{formatInr(r.confirmed_total, locale)}</span>}
            />
            <StatCard
              label={t("reportUnconfirmed")}
              value={formatCount(r.unconfirmed_count, locale)}
              unavailableLabel={tc("notAvailable")}
              hint={<span className="num">{formatInr(r.unconfirmed_total, locale)}</span>}
            />
            <StatCard
              label={t("reportOverdue")}
              value={formatCount(r.overdue_count, locale)}
              unavailableLabel={tc("notAvailable")}
            />
            <StatCard
              label={t("reportPeriod")}
              value={`${formatDate(r.date_from) ?? ""} – ${formatDate(r.date_to) ?? ""}`}
              unavailableLabel={tc("notAvailable")}
            />
          </dl>
        )}
      </FeesGate>
      <FeesGate data={exceptions}>
        {(e) => (
          <>
            <section aria-labelledby={unconfirmedId} className="space-y-3">
              <div className="space-y-1">
                <h2 id={unconfirmedId} className="text-lg font-semibold text-ink">
                  {t("unconfirmedTitle", { days: e.confirm_within_days })}
                </h2>
                <p className="text-sm text-ink-muted">{t("unconfirmedBody")}</p>
              </div>
              <DataTableV2
                id="fees-upi-unconfirmed"
                caption={t("unconfirmedTitle", { days: e.confirm_within_days })}
                captionHidden
                columns={unconfirmedColumns}
                state={{ status: "ready", data: e.unconfirmed_receipts }}
                rowKey={(r) => r.id}
                emptyTitle={t("noUnconfirmed")}
                stacked
                {...(e.unconfirmed_count > e.unconfirmed_receipts.length
                  ? {
                      footer: (
                        <p className="num text-sm text-ink-muted">
                          {t("more", {
                            count: e.unconfirmed_count - e.unconfirmed_receipts.length,
                          })}
                        </p>
                      ),
                    }
                  : {})}
              />
            </section>
            <Card
              title={t("creditsTitle")}
              description={t("creditsBody")}
              actions={
                <Button
                  variant="secondary"
                  onClick={() => rematch.mutate(undefined)}
                  disabled={rematch.isPending}
                >
                  {rematch.isPending ? tc("working") : t("rematch")}
                </Button>
              }
            >
              <div className="space-y-3">
                {rematch.data ? (
                  <Alert tone="success" live>
                    {t("rematched", { count: rematch.data.proposed })}
                  </Alert>
                ) : null}
                <ApiErrorAlert error={rematch.error ?? undefined} namespace="fees" />
                {e.proposed_count > 0 ? (
                  <Alert tone="info">{t("proposedWaiting", { count: e.proposed_count })}</Alert>
                ) : null}
                {e.lines.length === 0 ? (
                  <p>{t("noCreditExceptions")}</p>
                ) : (
                  <LinesTable lines={e.lines} caption={t("creditsTitle")} />
                )}
              </div>
            </Card>
          </>
        )}
      </FeesGate>
    </div>
  );
}

/**
 * `/fees/reconcile` (US-2109 AC2-AC3; FR-FEE-016/017; docs/17 §5.8): the Bank statement tab
 * (upload, proposed matches with checkboxes, "Confirm N matches") and UPI exceptions (receipts
 * not seen in the bank, credits nobody matched, the confirmed vs unconfirmed report). Holders of
 * `fee.reconcile` only. Online payments (R2-2D) have their own page, linked from here.
 */
export function ReconcileScreen() {
  const t = useTranslations("fees.reconcile");
  const tn = useTranslations("school.nav");
  const tu = useTranslations("ui");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can(RECONCILE);
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        me !== undefined && allowed ? (
          <Menu
            label={tu("moreActions")}
            variant="button"
            trigger={tu("more")}
            align="end"
            entries={[
              {
                kind: "link",
                id: "online",
                label: t("onlineMenu"),
                href: "/fees/reconcile/online",
                icon: "globe",
              },
              { kind: "link", id: "upi", label: t("upiSettingsLink"), href: "/settings/upi" },
            ]}
          />
        ) : null
      }
    />
  );
  if (me === undefined) return header;
  if (!allowed) {
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
      <Tabs
        label={t("tabs")}
        items={[
          { id: "bank", label: t("bankTab"), panel: <BankTab /> },
          { id: "exceptions", label: t("exceptionsTab"), panel: <ExceptionsTab /> },
        ]}
      />
    </div>
  );
}
