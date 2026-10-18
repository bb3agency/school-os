"use client";

import { useTranslations } from "next-intl";
import { useId, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorSummary, type SummaryError } from "@/components/ui/ErrorSummary";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SaveBar } from "@/components/ui/SaveBar";
import { unwrap, useApiMutation, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { ifMatch, MANAGE } from "./data";
import { FeesGate, NoAccess } from "./parts";
import { QR_PATH, UPI_KEYS, UPI_READERS, useUpiSettings, VPA, type UpiSettings } from "./upi";

type Errors = Partial<Record<"payee" | "vpa" | "days", string>>;

/** The school's static UPI QR as parents and the counter see it (no amount in it). */
export function UpiQr({ settings, size = 192 }: { settings: UpiSettings; size?: number }) {
  const t = useTranslations("fees.upi");
  if (!settings.configured) return null;
  return (
    <figure className="inline-flex flex-col items-center gap-2 rounded-lg border border-border bg-white p-3">
      {/* An API image (strict CSP, image/svg+xml) through the BFF; no amount is encoded. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`${QR_PATH}?v=${settings.version}`}
        width={size}
        height={size}
        alt={t("qrAlt", { payee: settings.payee_name ?? "" })}
      />
      <figcaption className="text-center text-sm">
        <span className="block font-semibold text-ink">{settings.payee_name}</span>
        <span className="block font-mono text-ink-muted">{settings.vpa}</span>
      </figcaption>
    </figure>
  );
}

function SettingsForm({ settings }: { settings: UpiSettings }) {
  const t = useTranslations("fees.upi");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [payee, setPayee] = useState(settings.payee_name ?? "");
  const [vpa, setVpa] = useState(settings.vpa ?? "");
  const [days, setDays] = useState(String(settings.confirm_within_days));
  const [errors, setErrors] = useState<Errors>({});
  const [attempt, setAttempt] = useState(0);
  const [saved, setSaved] = useState(false);
  const formId = useId();
  const ids = { payee: `${formId}-payee`, vpa: `${formId}-vpa`, days: `${formId}-days` };
  const dirty =
    payee !== (settings.payee_name ?? "") ||
    vpa !== (settings.vpa ?? "") ||
    days !== String(settings.confirm_within_days);
  const summary: SummaryError[] = (["payee", "vpa", "days"] as const).flatMap((key) => {
    const message = errors[key];
    return message ? [{ fieldId: ids[key], message }] : [];
  });
  const save = useApiMutation(
    (body: { payee_name: string; vpa: string; confirm_within_days: number }) =>
      unwrap(
        api.PUT("/api/v1/fees/upi-settings", {
          body,
          headers: settings.version ? { "If-Match": ifMatch(settings.version) } : {},
        }),
      ),
    { invalidate: [UPI_KEYS.settings], onSuccess: () => setSaved(true) },
  );

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaved(false);
    const next: Errors = {};
    const name = payee.trim().replace(/\s+/g, " ");
    const id = vpa.trim();
    const window = Number(days);
    if (name.length < 2 || name.length > 60) next.payee = t("errors.payee");
    if (!VPA.test(id)) next.vpa = t("errors.vpa");
    if (!Number.isInteger(window) || window < 1 || window > 30) next.days = t("errors.days");
    setErrors(next);
    if (Object.keys(next).length > 0) {
      setAttempt((n) => n + 1);
      return;
    }
    save.mutate({ payee_name: name, vpa: id, confirm_within_days: window });
  }

  return (
    <form onSubmit={onSubmit} noValidate aria-label={t("formTitle")} className="space-y-4">
      {summary.length > 0 ? <ErrorSummary errors={summary} attempt={attempt} /> : null}
      <TextField
        id={ids.payee}
        name="payee_name"
        label={t("payee")}
        hint={t("payeeHint")}
        error={errors.payee}
        maxLength={60}
        autoComplete="off"
        value={payee}
        onChange={(event) => setPayee(event.target.value)}
      />
      <TextField
        id={ids.vpa}
        name="vpa"
        className="max-w-md"
        label={t("vpa")}
        hint={t("vpaHint")}
        error={errors.vpa}
        maxLength={130}
        autoComplete="off"
        spellCheck={false}
        value={vpa}
        onChange={(event) => setVpa(event.target.value)}
      />
      <TextField
        id={ids.days}
        name="confirm_within_days"
        type="number"
        className="max-w-40"
        label={t("days")}
        hint={t("daysHint")}
        error={errors.days}
        min={1}
        max={30}
        inputMode="numeric"
        value={days}
        onChange={(event) => setDays(event.target.value)}
      />
      {saved ? (
        <Alert tone="success" live>
          {t("saved")}
        </Alert>
      ) : null}
      <ApiErrorAlert error={save.error ?? undefined} namespace="fees" />
      <SaveBar dirty={dirty}>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? tc("working") : t("save")}
        </Button>
        <Button
          variant="secondary"
          disabled={!dirty || save.isPending}
          onClick={() => {
            setPayee(settings.payee_name ?? "");
            setVpa(settings.vpa ?? "");
            setDays(String(settings.confirm_within_days));
            setErrors({});
          }}
        >
          {t("discard")}
        </Button>
      </SaveBar>
    </form>
  );
}

/**
 * `/settings/upi` (US-2109 AC1; FR-FEE-015; docs/17 §5.8): the payee name and UPI ID once, a
 * preview of the static QR as parents and the counter will see it, and the days within which a
 * UPI receipt should be seen in a bank statement. Changing it needs `fee.manage`; collectors,
 * finance readers and reconcilers see it.
 */
export function UpiSettingsScreen() {
  const t = useTranslations("fees.upi");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can(UPI_READERS);
  const settings = useUpiSettings(me !== undefined && allowed);
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
      <Alert tone="info" title={t("moneyTitle")}>
        {t("moneyBody")}
      </Alert>
      <FeesGate data={settings}>
        {(s) => (
          <div className="grid items-start gap-4 lg:grid-cols-3">
            <Card title={t("formTitle")} className="min-w-0 lg:col-span-2">
              {can(MANAGE) ? (
                <SettingsForm settings={s} />
              ) : (
                <dl className="grid gap-2 sm:grid-cols-2">
                  <div>
                    <dt className="text-sm text-ink-muted">{t("payee")}</dt>
                    <dd>{s.payee_name ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-sm text-ink-muted">{t("vpa")}</dt>
                    <dd className="font-mono">{s.vpa ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-sm text-ink-muted">{t("days")}</dt>
                    <dd className="num">{t("daysValue", { days: s.confirm_within_days })}</dd>
                  </div>
                </dl>
              )}
            </Card>
            <Card title={t("previewTitle")} description={t("previewBody")} className="min-w-0">
              {s.configured ? (
                <div className="flex justify-center">
                  <UpiQr settings={s} />
                </div>
              ) : (
                <EmptyState icon="camera" title={t("notSet")} />
              )}
            </Card>
          </div>
        )}
      </FeesGate>
    </div>
  );
}
