"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { saveText } from "@/features/fees/data";
import { FeesGate } from "@/features/fees/parts";
import type { Locale } from "@/i18n/routing";
import { useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatDateTime, formatInr } from "@/lib/format";
import { isConnectorOff } from "./data";
import {
  CONFIGURE,
  FINANCE_READ,
  REPORT,
  VOUCHER_KEYS,
  downloadExport,
  fieldId,
  previewExport,
  saveSettings,
  useLedgerCheck,
  useVoucherExports,
  useVoucherSettings,
  type PartyMode,
  type ReceiptMode,
  type VoucherPreview,
  type VoucherSettings,
} from "./vouchers-data";

/**
 * FM-03 (FR-TALLY-011..030): map Tally ledgers, download a voucher file for closed days, see
 * earlier files and compare balances with Tally. SchoolOS never writes to Tally: the accountant
 * imports the file in Tally (Gateway of Tally > Import > Vouchers). Every rule is the API's;
 * these permissions only decide which blocks the screen offers.
 */
export function TallyVouchersScreen() {
  const t = useTranslations("tally.vouchers");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([CONFIGURE, REPORT]);
  const settings = useVoucherSettings(me !== undefined && allowed);
  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {me !== undefined && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <FeesGate data={settings}>
          {(value) => (
            <>
              {value.format_verified ? null : (
                <Alert tone="info" title={t("unverifiedTitle")}>
                  {t("unverifiedBody")}
                </Alert>
              )}
              <MappingCard
                key={`${value.version}-${value.heads.length}`}
                settings={value}
                editable={can(CONFIGURE)}
              />
              {can(REPORT) ? <ExportCard configured={value.configured} /> : null}
              <HistoryCard />
              {can(FINANCE_READ) ? <CheckCard /> : null}
            </>
          )}
        </FeesGate>
      )}
    </div>
  );
}

// --- mapping ------------------------------------------------------------------------------------

function MappingCard({ settings, editable }: { settings: VoucherSettings; editable: boolean }) {
  const t = useTranslations("tally.vouchers");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [partyMode, setPartyMode] = useState<PartyMode>(settings.party_mode);
  const [includeDemand, setIncludeDemand] = useState(settings.include_demand);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState(false);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const text = (name: string) => String(form.get(name) ?? "").trim();
    const modeLedgers: Partial<Record<ReceiptMode, string>> = {};
    for (const m of settings.modes) {
      const value = text(`mode-${m.mode}`);
      if (value) modeLedgers[m.mode] = value;
    }
    const headLedgers: Record<string, string> = {};
    for (const h of settings.heads) {
      const value = text(`head-${h.head_id}`);
      if (value) headLedgers[h.head_id] = value;
    }
    setPending(true);
    setError(undefined);
    setSaved(false);
    saveSettings(
      api,
      {
        party_mode: partyMode,
        receivable_ledger: text("receivable_ledger"),
        mode_ledgers: modeLedgers,
        head_ledgers: headLedgers,
        other_income_ledger: text("other_income_ledger") || null,
        include_demand: includeDemand,
        company: text("company") || null,
      },
      settings.version,
    )
      .then(async () => {
        setSaved(true);
        await queryClient.invalidateQueries({ queryKey: VOUCHER_KEYS.all });
      })
      .catch((failure: unknown) => setError(failure))
      .finally(() => setPending(false));
  };

  return (
    <Card title={t("mapping.title")} description={t("mapping.description")}>
      <form onSubmit={submit} className="space-y-4" aria-describedby="tally-voucher-mapping-hint">
        <p id="tally-voucher-mapping-hint" className="text-sm text-ink-muted">
          {settings.configured ? t("mapping.exactNames") : t("mapping.notSaved")}
        </p>
        <fieldset disabled={!editable || pending} className="grid gap-4 md:grid-cols-2">
          <SelectField
            label={t("mapping.partyMode")}
            hint={
              partyMode === "student"
                ? settings.links_available
                  ? t("mapping.partyStudentHint")
                  : t("mapping.partyStudentNoLinksHint")
                : t("mapping.partyReceivableHint")
            }
            value={partyMode}
            options={[
              { value: "student", label: t("mapping.partyStudent") },
              { value: "receivable", label: t("mapping.partyReceivable") },
            ]}
            onChange={(e) =>
              setPartyMode(e.target.value === "receivable" ? "receivable" : "student")
            }
          />
          <TextField
            label={t("mapping.receivable")}
            name="receivable_ledger"
            id={fieldId("receivable_ledger")}
            defaultValue={settings.receivable_ledger}
            required
            maxLength={100}
            autoComplete="off"
          />
          {settings.modes.map((m) => (
            <TextField
              key={m.mode}
              label={t("mapping.modeLedger", { mode: t(`modes.${m.mode}`) })}
              name={`mode-${m.mode}`}
              id={fieldId(`mode_ledgers.${m.mode}`)}
              defaultValue={m.ledger ?? ""}
              maxLength={100}
              autoComplete="off"
            />
          ))}
          <TextField
            label={t("mapping.company")}
            hint={t("mapping.companyHint")}
            name="company"
            id={fieldId("company")}
            defaultValue={settings.company ?? ""}
            maxLength={100}
            autoComplete="off"
          />
        </fieldset>
        <fieldset disabled={!editable || pending} className="space-y-3">
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1 size-4 accent-primary"
              checked={includeDemand}
              onChange={(e) => setIncludeDemand(e.target.checked)}
            />
            <span>
              {t("mapping.includeDemand")}
              <span className="block text-sm text-ink-muted">{t("mapping.includeDemandHint")}</span>
            </span>
          </label>
          {includeDemand ? (
            <div className="grid gap-4 md:grid-cols-2">
              {settings.heads.map((h) => (
                <TextField
                  key={h.head_id}
                  label={t("mapping.headLedger", { head: `${h.code} ${h.name}` })}
                  name={`head-${h.head_id}`}
                  id={fieldId(`head_ledgers.${h.head_id}`)}
                  defaultValue={h.ledger ?? ""}
                  maxLength={100}
                  autoComplete="off"
                />
              ))}
              <TextField
                label={t("mapping.otherIncome")}
                name="other_income_ledger"
                id={fieldId("other_income_ledger")}
                defaultValue={settings.other_income_ledger ?? ""}
                maxLength={100}
                autoComplete="off"
              />
            </div>
          ) : null}
        </fieldset>
        {error !== undefined ? <ApiErrorAlert error={error} /> : null}
        {saved ? (
          <p role="status" className="text-sm text-ink">
            {t("mapping.saved")}
          </p>
        ) : null}
        {editable ? (
          <Button type="submit" loading={pending} disabled={pending}>
            {pending ? t("mapping.saving") : t("mapping.save")}
          </Button>
        ) : (
          <p className="text-sm text-ink-muted">{t("mapping.readOnly")}</p>
        )}
      </form>
    </Card>
  );
}

// --- export -------------------------------------------------------------------------------------

function ExportCard({ configured }: { configured: boolean }) {
  const t = useTranslations("tally.vouchers");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [includeExported, setIncludeExported] = useState(false);
  const [preview, setPreview] = useState<VoucherPreview | null>(null);
  const [busy, setBusy] = useState<"preview" | "download" | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const [done, setDone] = useState(false);
  const request = { period_from: from, period_to: to || from, include_exported: includeExported };

  const check = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy("preview");
    setError(undefined);
    setDone(false);
    previewExport(api, request)
      .then(setPreview)
      .catch((failure: unknown) => setError(failure))
      .finally(() => setBusy(null));
  };
  const download = () => {
    setBusy("download");
    setError(undefined);
    downloadExport(api, request)
      .then(async ({ name, text }) => {
        saveText(name, text, "application/xml;charset=utf-8");
        setDone(true);
        setPreview(null);
        await queryClient.invalidateQueries({ queryKey: VOUCHER_KEYS.exports });
      })
      .catch((failure: unknown) => setError(failure))
      .finally(() => setBusy(null));
  };
  const count = (n: number) => formatCount(n, locale) ?? String(n);

  return (
    <Card title={t("export.title")} description={t("export.description")}>
      {!configured ? (
        <Alert tone="info" title={t("export.notConfiguredTitle")}>
          {t("export.notConfiguredBody")}
        </Alert>
      ) : null}
      <form onSubmit={check} className="mt-4 flex flex-wrap items-end gap-4">
        <TextField
          label={t("export.from")}
          type="date"
          value={from}
          onChange={(e) => {
            setFrom(e.target.value);
            setPreview(null);
          }}
          required
        />
        <TextField
          label={t("export.to")}
          type="date"
          value={to}
          min={from || undefined}
          onChange={(e) => {
            setTo(e.target.value);
            setPreview(null);
          }}
        />
        <label className="flex items-center gap-2 pb-2">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={includeExported}
            onChange={(e) => {
              setIncludeExported(e.target.checked);
              setPreview(null);
            }}
          />
          {t("export.includeExported")}
        </label>
        <Button
          type="submit"
          variant="secondary"
          loading={busy === "preview"}
          disabled={busy !== null}
        >
          {t("export.check")}
        </Button>
      </form>
      {preview ? (
        <div className="mt-4 space-y-3" aria-live="polite">
          <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            {preview.by_kind
              .filter((k) => k.count > 0)
              .map((k) => (
                <div key={k.kind} className="flex justify-between gap-4">
                  <dt>{t(`kinds.${k.kind}`)}</dt>
                  <dd className="num">
                    {t("export.newOf", { new: count(k.new), all: count(k.count) })}
                  </dd>
                </div>
              ))}
            <div className="flex justify-between gap-4">
              <dt>{t("export.cashIn")}</dt>
              <dd className="num">{formatInr(preview.cash_in, locale)}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt>{t("export.cashOut")}</dt>
              <dd className="num">{formatInr(preview.cash_out, locale)}</dd>
            </div>
          </dl>
          {preview.vouchers === 0 ? <p className="text-sm">{t("export.empty")}</p> : null}
          {preview.open_days.length > 0 ? (
            <Alert tone="warning" title={t("export.openDaysTitle")}>
              {t("export.openDaysBody", {
                days: preview.open_days.map((d) => formatDate(d) ?? d).join(", "),
              })}
            </Alert>
          ) : null}
          {preview.missing.length > 0 ? (
            <Alert tone="warning" title={t("export.missingTitle")}>
              <ul className="list-disc pl-5">
                {preview.missing.map((m) => (
                  <li key={m.field}>
                    <a className="underline" href={`#${fieldId(m.field)}`}>
                      {m.field.startsWith("mode_ledgers.")
                        ? t("mapping.modeLedger", {
                            mode: t(
                              `modes.${m.field.slice("mode_ledgers.".length) as ReceiptMode}`,
                            ),
                          })
                        : m.label}
                    </a>
                  </li>
                ))}
              </ul>
            </Alert>
          ) : null}
          {preview.students_on_receivable > 0 ? (
            <p className="text-sm text-ink-muted">
              {t("export.onReceivable", { count: preview.students_on_receivable })}
            </p>
          ) : null}
          {preview.already_exported > 0 && !includeExported ? (
            <p className="text-sm text-ink-muted">
              {t("export.alreadyExported", { count: preview.already_exported })}
            </p>
          ) : null}
          <Button
            onClick={download}
            loading={busy === "download"}
            disabled={!preview.ready || busy !== null}
          >
            {t("export.download")}
          </Button>
          <p className="text-sm text-ink-muted">{t("export.importHint")}</p>
        </div>
      ) : null}
      {done ? (
        <p role="status" className="mt-4 text-sm">
          {t("export.done")}
        </p>
      ) : null}
      {error !== undefined ? <ApiErrorAlert error={error} className="mt-4" /> : null}
    </Card>
  );
}

// --- history ------------------------------------------------------------------------------------

function HistoryCard() {
  const t = useTranslations("tally.vouchers");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const exports = useVoucherExports(can([REPORT, FINANCE_READ]));
  if (!can([REPORT, FINANCE_READ])) return null;
  return (
    <Card title={t("history.title")}>
      {exports.status === "loading" ? <LoadingState label={t("history.loading")} /> : null}
      {exports.status === "ready" && exports.data.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("history.empty")}</p>
      ) : null}
      {exports.status === "ready" && exports.data.length > 0 ? (
        <TableScroll label={t("history.title")}>
          <Table>
            <THead>
              <Tr>
                <Th>{t("history.period")}</Th>
                <Th className="text-right">{t("history.vouchers")}</Th>
                <Th className="text-right">{t("history.net")}</Th>
                <Th>{t("history.by")}</Th>
              </Tr>
            </THead>
            <TBody>
              {exports.data.map((x) => (
                <Tr key={x.id}>
                  <Td>
                    {formatDate(x.period_from)} – {formatDate(x.period_to)}
                    {x.include_exported ? (
                      <span className="block text-xs text-ink-muted">{t("history.repeat")}</span>
                    ) : null}
                  </Td>
                  <Td className="num text-right">
                    {t("export.newOf", {
                      new: formatCount(x.new_vouchers, locale) ?? "",
                      all: formatCount(x.vouchers, locale) ?? "",
                    })}
                  </Td>
                  <Td className="num text-right">{formatInr(x.cash_total, locale)}</Td>
                  <Td>
                    {x.created_by_name ?? "—"}
                    <span className="block text-xs text-ink-muted">
                      {formatDateTime(x.created_at)}
                    </span>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableScroll>
      ) : null}
      {exports.status === "error" || exports.status === "unavailable" ? (
        <p className="text-sm text-ink-muted">{t("history.loadError")}</p>
      ) : null}
    </Card>
  );
}

// --- ledger-vs-Tally check ----------------------------------------------------------------------

function CheckCard() {
  const t = useTranslations("tally.vouchers");
  const locale = useLocale() as Locale;
  const check = useLedgerCheck(true);
  return (
    <Card title={t("check.title")} description={t("check.description")}>
      {check.status === "loading" ? <LoadingState label={t("check.loading")} /> : null}
      {isConnectorOff(check) ? <p className="text-sm text-ink-muted">{t("check.off")}</p> : null}
      {check.status === "ready" && check.data.as_of === null ? (
        <p className="text-sm text-ink-muted">{t("check.noSync")}</p>
      ) : null}
      {check.status === "ready" && check.data.as_of !== null ? (
        <div className="space-y-3">
          <p className="text-sm">
            {t("check.summary", {
              asOf: formatDate(check.data.as_of) ?? check.data.as_of,
              compared: check.data.compared,
              differing: check.data.differing,
            })}
          </p>
          {check.data.not_exported > 0 ? (
            <p className="text-sm text-ink-muted">
              {t("check.notExported", { count: check.data.not_exported })}
            </p>
          ) : null}
          {check.data.rows.length > 0 ? (
            <TableScroll label={t("check.title")}>
              <Table>
                <THead>
                  <Tr>
                    <Th>{t("check.ledger")}</Th>
                    <Th className="text-right">{t("check.tally")}</Th>
                    <Th className="text-right">{t("check.schoolos")}</Th>
                    <Th className="text-right">{t("check.difference")}</Th>
                  </Tr>
                </THead>
                <TBody>
                  {check.data.rows.map((r) => (
                    <Tr key={r.ledger_name}>
                      <Td>
                        {r.ledger_name}
                        <span className="block text-xs text-ink-muted">
                          {t(`check.states.${r.state}`)}
                        </span>
                      </Td>
                      <Td className="num text-right">
                        {r.tally_balance === null ? "—" : formatInr(r.tally_balance, locale)}
                      </Td>
                      <Td className="num text-right">
                        {r.schoolos_balance === null ? "—" : formatInr(r.schoolos_balance, locale)}
                      </Td>
                      <Td className="num text-right">{formatInr(r.difference, locale)}</Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableScroll>
          ) : (
            <p className="text-sm">{t("check.allMatch")}</p>
          )}
        </div>
      ) : null}
    </Card>
  );
}
