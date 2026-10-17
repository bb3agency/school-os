"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Toggle } from "@/components/ui/Toggle";
import { Value } from "@/components/ui/Value";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { useApiForm } from "@/lib/forms";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import {
  CONFIGURE,
  KEYS,
  MESSAGING_PERMISSIONS,
  READ,
  hhmm,
  ifMatch,
  isUnavailable,
  manualSchema,
  settingsBody,
  settingsSchema,
  useAccount,
  useMessagingSettings,
  type Account,
  type Settings,
  type Template,
} from "./data";
import { runEmbeddedSignup } from "./embedded-signup";

/** Loading, "not offered here" (404 module_unavailable), an error, or the content. */
export function MessagingGate<T>({
  data,
  children,
}: {
  data: Loadable<T>;
  children: (value: T) => ReactNode;
}) {
  const t = useTranslations("messaging");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  if (data.status === "loading") return <LoadingState label={tc("loading")} />;
  if (isUnavailable(data)) {
    return (
      <Alert tone="info" title={t("off.title")}>
        {t("off.body")}
      </Alert>
    );
  }
  if (data.status === "unavailable") {
    return (
      <Alert tone="info" title={tc("notAvailableYetTitle")}>
        {tc("notAvailableYetBody")}
      </Alert>
    );
  }
  if (data.status === "error") {
    return (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {data.reason ? te(`load.${data.reason}`) : tc("loadErrorBody")}
      </Alert>
    );
  }
  return <>{children(data.data)}</>;
}

function Fact({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="mt-0.5 break-words text-ink">{children}</dd>
    </div>
  );
}

const STATUS_PILL: Record<string, PillVariant> = {
  active: "done",
  paused: "negative",
  connecting: "progress",
  disconnected: "sample",
};

const TEMPLATE_PILL: Record<string, PillVariant> = {
  approved: "done",
  pending: "progress",
  not_created: "sample",
  rejected: "negative",
  paused: "negative",
  disabled: "negative",
};

/** What the school must know before connecting (US-2201 AC2). */
function Checklist() {
  const t = useTranslations("messaging.connect");
  return (
    <Alert tone="info" title={t("checklistTitle")}>
      <ul className="list-disc space-y-1 pl-5">
        <li>{t("paysMeta")}</li>
        <li>{t("inr")}</li>
        <li>{t("limit")}</li>
        <li>{t("optin")}</li>
      </ul>
    </Alert>
  );
}

function EmbeddedSignupButton({ config }: { config: NonNullable<Account["embedded_signup"]> }) {
  const t = useTranslations("messaging.connect");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [cancelled, setCancelled] = useState(false);

  async function start() {
    setPending(true);
    setError(undefined);
    setCancelled(false);
    try {
      const result = await runEmbeddedSignup({
        app_id: config.app_id ?? "",
        config_id: config.config_id ?? "",
        version: config.version ?? "v4",
        graph_version: config.graph_version ?? "v23.0",
      });
      await unwrap(
        api.POST("/api/v1/messaging/account/embedded-signup", {
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: result,
        }),
      );
      await queryClient.invalidateQueries({ queryKey: KEYS.all });
    } catch (failure) {
      if (failure instanceof Error && failure.message === "cancelled") setCancelled(true);
      else setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-3">
      <Button
        variant="primary"
        disabled={pending}
        aria-disabled={pending || undefined}
        onClick={() => void start()}
      >
        {pending ? t("signupPending") : t("signup")}
      </Button>
      <p className="text-sm text-ink-muted">{t("signupHint")}</p>
      {cancelled ? (
        <p role="status" className="text-sm text-ink-muted">
          {t("signupCancelled")}
        </p>
      ) : null}
      <ApiErrorAlert error={error} namespace="messaging" />
    </div>
  );
}

function ConnectCard({ account, canConfigure }: { account: Account; canConfigure: boolean }) {
  const t = useTranslations("messaging.connect");
  const api = useBffClient("staff");
  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-4">
        <Checklist />
        {!canConfigure ? (
          <p className="text-sm text-ink-muted">{t("askOwner")}</p>
        ) : (
          <>
            {account.embedded_signup ? (
              <EmbeddedSignupButton config={account.embedded_signup} />
            ) : null}
            {account.manual_token_allowed ? (
              <div className="space-y-2">
                <p className="text-sm text-ink-muted">{t("manualHint")}</p>
                <ActionDialog
                  triggerLabel={t("manual")}
                  triggerVariant={account.embedded_signup ? "secondary" : "primary"}
                  title={t("manualTitle")}
                  description={t("manualDescription")}
                  confirmLabel={t("connect")}
                  stepUp
                  schema={manualSchema}
                  invalidate={[KEYS.all]}
                  errorNamespace="messaging"
                  submit={(input, key) =>
                    unwrap(
                      api.POST("/api/v1/messaging/account/manual", {
                        headers: { "Idempotency-Key": key },
                        body: input,
                      }),
                    )
                  }
                >
                  {(errors) => (
                    <>
                      <TextField
                        name="token"
                        type="password"
                        label={t("token")}
                        hint={t("tokenHint")}
                        error={errors.token}
                        autoComplete="off"
                        spellCheck={false}
                      />
                      <TextField
                        name="waba_id"
                        label={t("wabaId")}
                        error={errors.waba_id}
                        inputMode="numeric"
                        autoComplete="off"
                      />
                      <TextField
                        name="phone_number_id"
                        label={t("phoneNumberId")}
                        error={errors.phone_number_id}
                        inputMode="numeric"
                        autoComplete="off"
                      />
                    </>
                  )}
                </ActionDialog>
              </div>
            ) : null}
            {!account.embedded_signup && !account.manual_token_allowed ? (
              <p className="text-sm text-ink-muted">{t("notConfigured")}</p>
            ) : null}
          </>
        )}
      </div>
    </Card>
  );
}

function AccountCard({ account, canConfigure }: { account: Account; canConfigure: boolean }) {
  const t = useTranslations("messaging.account");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const none = t("none");
  const status = account.status ?? "connecting";
  return (
    <Card
      title={t("title")}
      description={t("description")}
      actions={
        canConfigure ? (
          <span className="flex flex-wrap gap-2">
            {status === "paused" ? (
              <ActionDialog
                triggerLabel={t("resume")}
                triggerVariant="primary"
                title={t("resumeTitle")}
                description={t("resumeBody")}
                confirmLabel={t("resume")}
                stepUp
                schema={z.object({})}
                invalidate={[KEYS.all]}
                errorNamespace="messaging"
                submit={() => unwrap(api.POST("/api/v1/messaging/account/resume"))}
              />
            ) : null}
            <ActionDialog
              triggerLabel={t("disconnect")}
              triggerVariant="danger"
              title={t("disconnectTitle")}
              description={t("disconnectBody")}
              confirmLabel={t("disconnect")}
              confirmVariant="danger"
              stepUp
              schema={z.object({})}
              invalidate={[KEYS.all]}
              errorNamespace="messaging"
              submit={() =>
                unwrap(
                  api.DELETE("/api/v1/messaging/account", {
                    headers: { "If-Match": ifMatch(account.version ?? 0) },
                  }),
                )
              }
            />
          </span>
        ) : null
      }
    >
      {status === "paused" ? (
        <Alert tone="danger" title={t("pausedTitle")} className="mb-4">
          {t(`pause.${account.pause_reason ?? "other"}` as "pause.other")}
        </Alert>
      ) : null}
      {account.currency_state === "not_inr" ? (
        <Alert tone="warning" title={t("inrTitle")} className="mb-4">
          {t("inrBody", { date: formatDate(account.inr_required_from) ?? "" })}
        </Alert>
      ) : null}
      <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Fact label={t("number")}>{account.display_phone ?? none}</Fact>
        <Fact label={t("name")}>{account.display_name ?? none}</Fact>
        <Fact label={t("status")}>
          <Pill variant={STATUS_PILL[status] ?? "sample"}>{t(`statuses.${status}`)}</Pill>
        </Fact>
        <Fact label={t("quality")}>
          {account.quality_rating
            ? t(`qualities.${account.quality_rating}` as "qualities.GREEN")
            : none}
        </Fact>
        <Fact label={t("limit")}>
          {account.messaging_limit_count
            ? t("limitValue", {
                count: formatCount(account.messaging_limit_count, locale) ?? "",
                cap: formatCount(account.default_daily_cap ?? 0, locale) ?? "",
              })
            : none}
        </Fact>
        <Fact label={t("currency")}>
          {t(`currencies.${account.currency_state ?? "unknown"}` as "currencies.unknown")}
        </Fact>
        <Fact label={t("mode")}>{account.mode ? t(`modes.${account.mode}`) : none}</Fact>
        <Fact label={t("connectedAt")}>{formatDateTime(account.connected_at) ?? none}</Fact>
        <Fact label={t("syncedAt")}>{formatDateTime(account.last_synced_at) ?? none}</Fact>
      </dl>
    </Card>
  );
}

function TemplatesCard({
  templates,
  canConfigure,
}: {
  templates: Template[];
  canConfigure: boolean;
}) {
  const t = useTranslations("messaging.templates");
  const api = useBffClient("staff");
  return (
    <Card
      title={t("title")}
      description={t("description")}
      actions={
        canConfigure ? (
          <ActionDialog
            triggerLabel={t("sync")}
            title={t("syncTitle")}
            description={t("syncBody")}
            confirmLabel={t("sync")}
            stepUp
            schema={z.object({})}
            invalidate={[KEYS.all]}
            errorNamespace="messaging"
            submit={() => unwrap(api.POST("/api/v1/messaging/templates/sync"))}
          />
        ) : null
      }
    >
      <TableScroll label={t("title")}>
        <Table>
          <THead>
            <Tr>
              <Th>{t("columns.purpose")}</Th>
              <Th>{t("columns.language")}</Th>
              <Th>{t("columns.status")}</Th>
              <Th>{t("columns.review")}</Th>
            </Tr>
          </THead>
          <TBody>
            {templates.map((item) => (
              <Tr key={`${item.template_key}-${item.language}`}>
                <Td className="font-semibold">
                  {t(`purposes.${item.template_key}` as "purposes.school_notice")}
                </Td>
                <Td>{t(`languages.${item.language}`)}</Td>
                <Td>
                  <span className="flex flex-wrap gap-1">
                    <Pill variant={TEMPLATE_PILL[item.status] ?? "sample"}>
                      {t(`statuses.${item.status}`)}
                    </Pill>
                    {item.paused_by_sos ? (
                      <Pill variant="negative">{t("pausedBySchoolOs")}</Pill>
                    ) : null}
                  </span>
                </Td>
                <Td>
                  <Value>{item.reviewed ? t("reviewed") : t("notReviewed")}</Value>
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableScroll>
    </Card>
  );
}

function SettingsForm({ settings }: { settings: Settings }) {
  const t = useTranslations("messaging.settings");
  const api = useBffClient("staff");
  const [saved, setSaved] = useState(false);
  const form = useApiForm({
    schema: settingsSchema,
    invalidate: [KEYS.settings],
    submit: (input) =>
      unwrap(
        api.PUT("/api/v1/messaging/settings", {
          headers: { "If-Match": ifMatch(settings.version) },
          body: settingsBody(input),
        }),
      ),
    onSuccess: () => setSaved(true),
  });
  const switches = [
    ["notices_enabled", settings.notices_enabled],
    ["absence_alerts_enabled", settings.absence_alerts_enabled],
    ["fee_reminders_enabled", settings.fee_reminders_enabled],
    ["fee_receipts_enabled", settings.fee_receipts_enabled],
  ] as const;
  return (
    <form
      onSubmit={form.onSubmit}
      onChange={() => setSaved(false)}
      className="space-y-4"
      noValidate
    >
      <fieldset className="space-y-3">
        <legend className="font-semibold text-ink">{t("switches")}</legend>
        {switches.map(([name, on]) => (
          <Toggle
            key={name}
            name={name}
            label={t(`switch.${name}`)}
            description={t(`switchHint.${name}`)}
            defaultChecked={on}
            labelFirst
          />
        ))}
      </fieldset>
      <div className="grid gap-4 sm:grid-cols-3">
        <TextField
          name="absence_alert_time"
          type="time"
          label={t("absenceTime")}
          defaultValue={hhmm(settings.absence_alert_time)}
          error={form.errors.absence_alert_time}
        />
        <TextField
          name="quiet_start"
          type="time"
          label={t("quietStart")}
          defaultValue={hhmm(settings.quiet_start)}
          error={form.errors.quiet_start}
        />
        <TextField
          name="quiet_end"
          type="time"
          label={t("quietEnd")}
          defaultValue={hhmm(settings.quiet_end)}
          error={form.errors.quiet_end}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          name="daily_cap"
          label={t("dailyCap")}
          hint={
            settings.default_daily_cap
              ? t("dailyCapHint", { cap: String(settings.default_daily_cap) })
              : t("dailyCapHintUnknown")
          }
          inputMode="numeric"
          defaultValue={settings.daily_cap === null ? "" : String(settings.daily_cap)}
          error={form.errors.daily_cap}
        />
        <TextField
          name="office_phone"
          type="tel"
          label={t("officePhone")}
          hint={t("officePhoneHint")}
          defaultValue={settings.office_phone ?? ""}
          autoComplete="off"
          error={form.errors.office_phone}
        />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="submit"
          variant="primary"
          disabled={form.pending}
          aria-disabled={form.pending || undefined}
        >
          {t("save")}
        </Button>
        {saved ? (
          <p role="status" className="text-sm text-positive-ink">
            {t("saved")}
          </p>
        ) : null}
      </div>
      <ApiErrorAlert error={form.error} namespace="messaging" />
    </form>
  );
}

function SettingsCard({ canConfigure }: { canConfigure: boolean }) {
  const t = useTranslations("messaging.settings");
  const settings = useMessagingSettings();
  return (
    <Card title={t("title")} description={t("description")}>
      <MessagingGate data={settings}>
        {(current) =>
          canConfigure ? (
            <SettingsForm key={current.version} settings={current} />
          ) : (
            <dl className="grid gap-4 sm:grid-cols-2">
              <Fact label={t("switches")}>
                {[
                  current.notices_enabled ? t("switch.notices_enabled") : null,
                  current.absence_alerts_enabled ? t("switch.absence_alerts_enabled") : null,
                  current.fee_reminders_enabled ? t("switch.fee_reminders_enabled") : null,
                  current.fee_receipts_enabled ? t("switch.fee_receipts_enabled") : null,
                ]
                  .filter(Boolean)
                  .join(", ") || t("allOff")}
              </Fact>
              <Fact label={t("quiet")}>
                {t("quietValue", {
                  start: hhmm(current.quiet_start),
                  end: hhmm(current.quiet_end),
                })}
              </Fact>
            </dl>
          )
        }
      </MessagingGate>
    </Card>
  );
}

/**
 * WhatsApp settings (US-2201, US-2209; ADR-0043; docs/17 §5.8): connect the school's own
 * number (Meta's Embedded Signup, or a pasted token for pilots), the account's status, quality
 * and limit with Resume after a pause, template statuses, and switches and times. Changes need
 * `messaging.configure` with a recent MFA sign-in; `message.read` holders see the state.
 */
export function WhatsAppSettingsScreen() {
  const t = useTranslations("messaging");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const meLoaded = me !== undefined;
  const allowed = can(MESSAGING_PERMISSIONS);
  const canConfigure = can(CONFIGURE);
  const account = useAccount(meLoaded && can([CONFIGURE, READ]));
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("settingsTitle")}
        description={t("settingsDescription")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("settingsTitle") }]}
      />
      {meLoaded && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <MessagingGate data={meLoaded ? account : { status: "loading" }}>
          {(current) => (
            <>
              {current.unreviewed_texts && current.unreviewed_texts.length > 0 ? (
                <Alert tone="warning" title={t("unreviewedTitle")}>
                  {t("unreviewedBody")}
                </Alert>
              ) : null}
              {current.connected ? (
                <>
                  <AccountCard account={current} canConfigure={canConfigure} />
                  <TemplatesCard templates={current.templates ?? []} canConfigure={canConfigure} />
                </>
              ) : (
                <ConnectCard account={current} canConfigure={canConfigure} />
              )}
              <SettingsCard canConfigure={canConfigure} />
            </>
          )}
        </MessagingGate>
      )}
    </div>
  );
}
