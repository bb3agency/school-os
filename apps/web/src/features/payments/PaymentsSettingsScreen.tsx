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
import { SecretOnce } from "@/components/ui/SecretOnce";
import { Steps } from "@/components/ui/Steps";
import type { Locale } from "@/i18n/routing";
import { cn } from "@/lib/cn";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import {
  CONFIGURE,
  FINANCE_READ,
  KEYS,
  manualSchema,
  useConnection,
  type Connection,
  type ManualConnect,
  type WebhookSetup,
} from "./data";

/** Loading, "fees are off" (404 module_hidden), an error, or the content. */
export function PaymentsGate<T>({
  data,
  children,
}: {
  data: Loadable<T>;
  children: (value: T) => ReactNode;
}) {
  const t = useTranslations("payments");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  if (data.status === "loading") return <LoadingState label={tc("loading")} />;
  if (data.status === "error" && data.reason === "not_found") {
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

function Fact({
  label,
  children,
  numeric = false,
}: {
  label: ReactNode;
  children: ReactNode;
  /** Dates, times and counts: SOS Figures, never truncated. */
  numeric?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className={cn("mt-0.5 break-words text-ink", numeric && "num")}>{children}</dd>
    </div>
  );
}

/**
 * Where the school is in connecting (docs/17 §4.4 Steps), derived from the connection only:
 * not connected = choose how to connect; the one-time webhook panel = add the webhook;
 * connected = ready. The flow itself is unchanged.
 */
function ConnectSteps({ current }: { current: 0 | 1 | 2 }) {
  const t = useTranslations("payments.steps");
  return (
    <Steps
      label={t("label")}
      current={current}
      steps={[
        { id: "choose", label: t("choose") },
        { id: "webhook", label: t("webhook") },
        { id: "ready", label: t("ready") },
      ]}
    />
  );
}

const STATUS_PILL: Record<string, PillVariant> = {
  active: "done",
  revoked: "negative",
  disconnected: "sample",
};

/** What the school must know first (US-2107 AC2): its own account, its own fees. */
function MerchantNotice() {
  const t = useTranslations("payments.connect");
  return (
    <Alert tone="info" title={t("noticeTitle")}>
      <ul className="list-disc space-y-1 pl-5">
        <li>{t("ownAccount")}</li>
        <li>{t("neverHeld")}</li>
        <li>{t("razorpayFees")}</li>
        <li>{t("cashStays")}</li>
      </ul>
    </Alert>
  );
}

/** The webhook the school adds in its dashboard (manual keys), shown once with its secret. */
function WebhookInstructions({
  setup,
  onDone,
  showSteps = false,
}: {
  setup: WebhookSetup;
  onDone: () => void;
  /** First connection: show where this is in the connect steps. */
  showSteps?: boolean;
}) {
  const t = useTranslations("payments.webhook");
  return (
    <div className="space-y-4">
      {showSteps ? <ConnectSteps current={1} /> : null}
      <p className="text-sm">{t("steps")}</p>
      <div className="space-y-1">
        <p className="text-sm text-ink-muted">{t("url")}</p>
        <code className="block break-all rounded bg-surface-sunken p-2 font-mono text-sm">
          {setup.url}
        </code>
      </div>
      <div className="space-y-1">
        <p className="text-sm text-ink-muted">{t("events")}</p>
        <ul className="list-disc pl-5 font-mono text-sm">
          {setup.events.map((event) => (
            <li key={event}>{event}</li>
          ))}
        </ul>
      </div>
      {setup.previous_valid_until ? (
        <p className="num text-sm text-ink-muted">
          {t("previousUntil", { when: formatDateTime(setup.previous_valid_until) ?? "" })}
        </p>
      ) : null}
      <SecretOnce label={t("secret")} secret={setup.secret} onDone={onDone} doneLabel={t("done")} />
    </div>
  );
}

function OAuthButton() {
  const t = useTranslations("payments.connect");
  const api = useBffClient("staff");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  async function start() {
    setPending(true);
    setError(undefined);
    try {
      const out = await unwrap(api.POST("/api/v1/payments/connection/oauth/start"));
      window.location.assign(out.authorize_url);
    } catch (failure) {
      setError(failure);
      setPending(false);
    }
  }
  return (
    <div className="space-y-2">
      <Button
        variant="primary"
        disabled={pending}
        aria-disabled={pending || undefined}
        onClick={() => void start()}
      >
        {pending ? t("oauthPending") : t("oauth")}
      </Button>
      <p className="text-sm text-ink-muted">{t("oauthHint")}</p>
      <ApiErrorAlert error={error} namespace="payments" />
    </div>
  );
}

function ConnectCard({
  connection,
  canConfigure,
}: {
  connection: Connection;
  canConfigure: boolean;
}) {
  const t = useTranslations("payments.connect");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-4">
        <MerchantNotice />
        {connection.status === "revoked" ? (
          <Alert tone="danger" title={t("revokedTitle")}>
            {t("revokedBody")}
          </Alert>
        ) : null}
        {!canConfigure ? (
          <p className="text-sm text-ink-muted">{t("askOwner")}</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {connection.oauth_available ? (
              <div className="min-w-0 rounded-lg border border-border p-4">
                <OAuthButton />
              </div>
            ) : null}
            {connection.manual_keys_allowed ? (
              <div className="min-w-0 space-y-2 rounded-lg border border-border p-4">
                <p className="text-sm text-ink-muted">{t("manualHint")}</p>
                <ActionDialog
                  triggerLabel={t("manual")}
                  triggerVariant={connection.oauth_available ? "secondary" : "primary"}
                  title={t("manualTitle")}
                  description={t("manualDescription")}
                  confirmLabel={t("connect")}
                  stepUp
                  schema={manualSchema}
                  errorNamespace="payments"
                  submit={(input) =>
                    unwrap(
                      api.POST("/api/v1/payments/connection/manual", {
                        body: { key_id: input.key_id, key_secret: input.key_secret },
                      }),
                    )
                  }
                  renderResult={(result: ManualConnect, close) => (
                    <WebhookInstructions
                      showSteps
                      setup={result.webhook}
                      onDone={() => {
                        close();
                        void queryClient.invalidateQueries({ queryKey: KEYS.all });
                      }}
                    />
                  )}
                >
                  {(errors) => (
                    <>
                      <TextField
                        name="key_id"
                        label={t("keyId")}
                        hint={t("keyIdHint")}
                        error={errors.key_id}
                        autoComplete="off"
                        spellCheck={false}
                      />
                      <TextField
                        name="key_secret"
                        type="password"
                        label={t("keySecret")}
                        hint={t("keySecretHint")}
                        error={errors.key_secret}
                        autoComplete="off"
                        spellCheck={false}
                      />
                    </>
                  )}
                </ActionDialog>
              </div>
            ) : null}
            {!connection.oauth_available && !connection.manual_keys_allowed ? (
              <p className="text-sm text-ink-muted">{t("notConfigured")}</p>
            ) : null}
          </div>
        )}
      </div>
    </Card>
  );
}

function TestButton() {
  const t = useTranslations("payments.account");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<"ok" | "failed" | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  async function run() {
    setPending(true);
    setOutcome(null);
    setError(undefined);
    try {
      const out = await unwrap(api.POST("/api/v1/payments/connection/test"));
      setOutcome(out.ok ? "ok" : "failed");
      await queryClient.invalidateQueries({ queryKey: KEYS.all });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return (
    <span className="flex flex-col gap-1">
      <Button
        variant="secondary"
        disabled={pending}
        aria-disabled={pending || undefined}
        onClick={() => void run()}
      >
        {t("test")}
      </Button>
      <span role="status" className="text-sm">
        {outcome === "ok" ? t("testOk") : outcome === "failed" ? t("testFailed") : ""}
      </span>
      <ApiErrorAlert error={error} namespace="payments" />
    </span>
  );
}

function AccountCard({
  connection,
  canConfigure,
}: {
  connection: Connection;
  canConfigure: boolean;
}) {
  const t = useTranslations("payments.account");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const locale = useLocale() as Locale;
  const none = t("none");
  const status = connection.status ?? "active";
  return (
    <Card
      title={t("title")}
      description={t("description")}
      actions={
        canConfigure ? (
          <span className="flex flex-wrap items-start gap-2">
            <TestButton />
            <ActionDialog
              triggerLabel={t("rotate")}
              title={t("rotateTitle")}
              description={t("rotateBody")}
              confirmLabel={t("rotate")}
              consequence={t("rotateConsequence")}
              stepUp
              schema={z.object({})}
              errorNamespace="payments"
              submit={() => unwrap(api.POST("/api/v1/payments/connection/rotate-webhook-secret"))}
              renderResult={(result: WebhookSetup, close) => (
                <WebhookInstructions
                  setup={result}
                  onDone={() => {
                    close();
                    void queryClient.invalidateQueries({ queryKey: KEYS.all });
                  }}
                />
              )}
            />
          </span>
        ) : null
      }
    >
      {connection.failed_events ? (
        <Alert tone="warning" title={t("failedEventsTitle")} className="mb-4">
          {t("failedEventsBody", { count: connection.failed_events })}
        </Alert>
      ) : null}
      <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Fact label={t("status")}>
          <Pill variant={STATUS_PILL[status] ?? "sample"}>{t(`statuses.${status}`)}</Pill>
        </Fact>
        <Fact label={t("mode")}>{connection.mode ? t(`modes.${connection.mode}`) : none}</Fact>
        <Fact label={t("environment")}>
          {connection.environment ? t(`environments.${connection.environment}`) : none}
        </Fact>
        <Fact label={t("accountId")}>
          <span className="font-mono">
            {connection.provider_account_id ?? connection.key_id_hint ?? none}
          </span>
        </Fact>
        <Fact label={t("connectedAt")} numeric>
          {formatDateTime(connection.connected_at) ?? none}
          {connection.connected_by_name ? ` · ${connection.connected_by_name}` : ""}
        </Fact>
        <Fact label={t("checkedAt")} numeric>
          {formatDateTime(connection.last_checked_at) ?? none}
        </Fact>
        <Fact label={t("openLinks")} numeric>
          {formatCount(connection.open_links, locale)}
        </Fact>
        <Fact label={t("pendingEvents")} numeric>
          {formatCount(connection.pending_events, locale)}
        </Fact>
        {connection.mode === "oauth" ? (
          <Fact label={t("accessUntil")} numeric>
            {formatDateTime(connection.refresh_expires_at) ?? none}
          </Fact>
        ) : null}
      </dl>
      <div className="mt-4 space-y-1">
        <p className="text-sm text-ink-muted">{t("webhookUrl")}</p>
        <code className="block break-all rounded bg-surface-sunken p-2 font-mono text-sm">
          {connection.webhook_url ?? none}
        </code>
        {connection.previous_valid_until ? (
          <p className="num text-sm text-ink-muted">
            {t("previousUntil", { when: formatDateTime(connection.previous_valid_until) ?? "" })}
          </p>
        ) : null}
      </div>
    </Card>
  );
}

/** Disconnect (danger, step-up) on its own, with what it changes in one sentence. */
function DisconnectCard({ connection }: { connection: Connection }) {
  const t = useTranslations("payments.account");
  const api = useBffClient("staff");
  return (
    <Card
      title={t("disconnectTitle")}
      description={t("disconnectBody")}
      actions={
        <ActionDialog
          triggerLabel={t("disconnect")}
          triggerVariant="danger"
          title={t("disconnectTitle")}
          description={t("disconnectBody")}
          confirmLabel={t("disconnect")}
          confirmVariant="danger"
          consequence={t("disconnectConsequence", { count: connection.open_links })}
          stepUp
          schema={z.object({})}
          invalidate={[KEYS.all]}
          errorNamespace="payments"
          submit={() => unwrap(api.DELETE("/api/v1/payments/connection"))}
        />
      }
    />
  );
}

/**
 * Online payments settings (US-2107; FR-PAY-002..004, FR-PAY-012..014; ADR-0042; docs/17 §5.8):
 * connect the school's own Razorpay account (Razorpay sign-in once SchoolOS is an approved
 * partner; until then the pilot key mode), test it, rotate the webhook secret, disconnect.
 * Changes need `payments.configure` with a recent MFA sign-in (owner); `finance.read` holders
 * see the state. No secret is ever shown again after the dialog that showed it closes.
 */
export function PaymentsSettingsScreen() {
  const t = useTranslations("payments");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const meLoaded = me !== undefined;
  const allowed = can([CONFIGURE, FINANCE_READ]);
  const canConfigure = can(CONFIGURE);
  const connection = useConnection(meLoaded && allowed);
  return (
    <div className="space-y-5">
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
        <PaymentsGate data={meLoaded ? connection : { status: "loading" }}>
          {(current) => (
            <>
              <ConnectSteps current={current.connected ? 2 : 0} />
              {current.connected ? (
                <>
                  <AccountCard connection={current} canConfigure={canConfigure} />
                  {canConfigure ? <DisconnectCard connection={current} /> : null}
                </>
              ) : (
                <ConnectCard connection={current} canConfigure={canConfigure} />
              )}
            </>
          )}
        </PaymentsGate>
      )}
    </div>
  );
}
