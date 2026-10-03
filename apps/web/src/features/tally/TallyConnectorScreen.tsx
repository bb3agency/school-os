"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useState, type FormEvent } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatDateTime, formatInr } from "@/lib/format";
import {
  CONFIGURE,
  DEVICE_MANAGE,
  FINANCE_READ,
  KEYS,
  TALLY_PERMISSIONS,
  codeSchema,
  enrolCommand,
  ifMatch,
  useConnectorStatus,
  useDevices,
  useGroups,
  type ConnectorStatus,
  type Device,
  type EnrolmentCode,
  type Group,
} from "./data";
import { Fact, TallyGate } from "./parts";

/** The one-time code with what to run on the office PC. Kept in memory only, never stored. */
function CodeOnce({
  code,
  tenantId,
  onDone,
}: {
  code: EnrolmentCode;
  tenantId: string;
  onDone: () => void;
}) {
  const t = useTranslations("tally.enrol");
  const checkId = useId();
  const [stored, setStored] = useState(false);
  const origin = typeof window === "undefined" ? "https://" : window.location.origin;
  return (
    <div className="space-y-4">
      <Alert tone="warning" title={t("onceTitle")}>
        {t("onceBody", { time: formatDateTime(code.expires_at) ?? "" })}
      </Alert>
      <div className="space-y-1">
        <p className="text-sm text-ink-muted">{t("codeLabel")}</p>
        <p className="font-mono text-2xl tracking-widest break-all text-ink" lang="en">
          {code.code}
        </p>
      </div>
      <div className="space-y-1">
        <p className="text-sm text-ink-muted">{t("commandLabel")}</p>
        <pre className="overflow-x-auto rounded-md bg-surface-muted p-3 text-sm whitespace-pre-wrap break-all">
          <code lang="en">{enrolCommand(origin, tenantId, code.code)}</code>
        </pre>
        <p className="text-sm text-ink-muted">{t("commandHint")}</p>
      </div>
      <div className="flex items-center gap-2">
        <input
          id={checkId}
          type="checkbox"
          className="size-5"
          checked={stored}
          onChange={(event) => setStored(event.target.checked)}
        />
        <label htmlFor={checkId} className="text-sm">
          {t("handedOver")}
        </label>
      </div>
      <Button
        variant="primary"
        disabled={!stored}
        aria-disabled={!stored || undefined}
        onClick={onDone}
      >
        {t("done")}
      </Button>
    </div>
  );
}

function StatusCard({ status }: { status: ConnectorStatus }) {
  const t = useTranslations("tally.status");
  const locale = useLocale() as Locale;
  const none = t("none");
  return (
    <Card title={t("title")}>
      {status.silent ? (
        <Alert tone="warning" title={t("silentTitle")} className="mb-4">
          {t("silentBody")}
        </Alert>
      ) : null}
      <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Fact label={t("agents")}>{formatCount(status.devices_active, locale)}</Fact>
        <Fact label={t("lastSync")}>{formatDateTime(status.last_sync_at) ?? none}</Fact>
        <Fact label={t("asOf")}>{formatDate(status.as_of) ?? none}</Fact>
        <Fact label={t("company")}>{status.company ?? none}</Fact>
        <Fact label={t("groups")}>{formatCount(status.groups_selected, locale)}</Fact>
        <Fact label={t("ledgers")}>
          {t("ledgersValue", {
            linked: formatCount(status.parties_linked, locale) ?? "0",
            total: formatCount(status.parties, locale) ?? "0",
          })}
        </Fact>
        {status.total_due !== null && status.total_due !== undefined ? (
          <Fact label={t("totalDue")}>{formatInr(status.total_due, locale)}</Fact>
        ) : null}
      </dl>
    </Card>
  );
}

function DeviceStatus({ device }: { device: Device }) {
  const t = useTranslations("tally.devices");
  return (
    <span className="flex flex-wrap gap-1">
      <Pill variant={device.status === "active" ? "done" : "sample"}>
        {t(`status.${device.status}`)}
      </Pill>
      {device.silent ? <Pill variant="negative">{t("silent")}</Pill> : null}
      {device.outdated ? <Pill variant="review">{t("outdated")}</Pill> : null}
    </span>
  );
}

function DevicesCard({ tenantId }: { tenantId: string }) {
  const t = useTranslations("tally.devices");
  const te = useTranslations("tally.enrol");
  const api = useBffClient("staff");
  const devices = useDevices(true);
  const invalidate = [KEYS.all] as const;
  return (
    <Card
      title={t("title")}
      description={t("description")}
      actions={
        <ActionDialog
          triggerLabel={te("add")}
          triggerVariant="primary"
          title={te("title")}
          description={te("description")}
          confirmLabel={te("create")}
          stepUp
          schema={codeSchema}
          invalidate={invalidate}
          errorNamespace="tally"
          submit={(input) =>
            unwrap(
              api.POST("/api/v1/tally/enrolment-codes", {
                body: { device_name: input.device_name },
              }),
            )
          }
          renderResult={(result, close) => (
            <CodeOnce code={result} tenantId={tenantId} onDone={close} />
          )}
        >
          {(errors) => (
            <TextField
              name="device_name"
              label={te("deviceName")}
              hint={te("deviceNameHint")}
              error={errors.device_name}
              defaultValue="Office PC"
              maxLength={80}
              autoComplete="off"
            />
          )}
        </ActionDialog>
      }
    >
      <TallyGate data={devices}>
        {(list) =>
          list.length === 0 ? (
            <EmptyState icon="key" title={t("emptyTitle")} body={t("emptyBody")} />
          ) : (
            <TableScroll label={t("title")}>
              <Table>
                <THead>
                  <Tr>
                    <Th>{t("columns.name")}</Th>
                    <Th>{t("columns.status")}</Th>
                    <Th>{t("columns.lastSeen")}</Th>
                    <Th>{t("columns.lastSync")}</Th>
                    <Th>{t("columns.version")}</Th>
                    <Th>{t("columns.tally")}</Th>
                    <Th>
                      <span className="sr-only">{t("columns.actions")}</span>
                    </Th>
                  </Tr>
                </THead>
                <TBody>
                  {list.map((device) => (
                    <Tr key={device.id}>
                      <Td className="font-semibold">{device.name}</Td>
                      <Td>
                        <DeviceStatus device={device} />
                      </Td>
                      <Td>
                        <Value>{formatDateTime(device.last_seen_at)}</Value>
                      </Td>
                      <Td>
                        <Value>{formatDateTime(device.last_sync_at)}</Value>
                      </Td>
                      <Td>
                        <Value>{device.agent_version}</Value>
                      </Td>
                      <Td>
                        <Value>{device.tally_product}</Value>
                      </Td>
                      <Td>
                        {device.status === "active" ? (
                          <ActionDialog
                            triggerLabel={t("revoke")}
                            triggerVariant="danger"
                            triggerSize="sm"
                            triggerDescription={device.name}
                            title={t("revokeTitle")}
                            description={t("revokeBody")}
                            confirmLabel={t("revoke")}
                            confirmVariant="danger"
                            stepUp
                            schema={z.object({})}
                            invalidate={invalidate}
                            errorNamespace="tally"
                            submit={() =>
                              unwrap(
                                api.POST("/api/v1/tally/devices/{device_id}/revoke", {
                                  params: { path: { device_id: device.id } },
                                  headers: { "If-Match": ifMatch(device.version) },
                                }),
                              )
                            }
                          />
                        ) : (
                          <Value>{formatDate(device.revoked_at)}</Value>
                        )}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableScroll>
          )
        }
      </TallyGate>
    </Card>
  );
}

function GroupsForm({ groups, company }: { groups: Group[]; company: string }) {
  const t = useTranslations("tally.groups");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const present = groups.filter((g) => g.company === company && g.present);
  const [chosen, setChosen] = useState<Set<string>>(
    () => new Set(present.filter((g) => g.selected).map((g) => g.id)),
  );
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const legendId = useId();

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(undefined);
    try {
      await unwrap(
        api.PUT("/api/v1/tally/groups/selection", {
          body: { company, group_ids: [...chosen] },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: KEYS.all });
      setSaved(true);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  function toggle(id: string, on: boolean) {
    setSaved(false);
    setChosen((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  return (
    <form onSubmit={(event) => void save(event)} className="space-y-4" noValidate>
      <Alert tone="info" title={t("privacyTitle")}>
        {t("privacyBody")}
      </Alert>
      <fieldset aria-labelledby={legendId} className="space-y-2">
        <legend id={legendId} className="font-semibold text-ink">
          {t("legend", { company })}
        </legend>
        <ul className="grid gap-2 sm:grid-cols-2">
          {present.map((group) => (
            <li key={group.id} className="flex items-start gap-2">
              <input
                id={`group-${group.id}`}
                type="checkbox"
                className="mt-0.5 size-5 shrink-0"
                checked={chosen.has(group.id)}
                onChange={(event) => toggle(group.id, event.target.checked)}
              />
              <label htmlFor={`group-${group.id}`} className="min-w-0 break-words">
                <span className="text-ink">{group.name}</span>
                {group.parent ? (
                  <span className="block text-sm text-ink-muted">
                    {t("under", { parent: group.parent })}
                  </span>
                ) : null}
              </label>
            </li>
          ))}
        </ul>
      </fieldset>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="submit"
          variant="primary"
          disabled={pending}
          aria-disabled={pending || undefined}
        >
          {t("save")}
        </Button>
        {saved ? (
          <p role="status" className="text-sm text-positive-ink">
            {t("saved", { count: chosen.size })}
          </p>
        ) : null}
      </div>
      <ApiErrorAlert error={error} namespace="tally" />
    </form>
  );
}

function GroupsCard({ company }: { company: string | null | undefined }) {
  const t = useTranslations("tally.groups");
  const groups = useGroups(true);
  return (
    <Card title={t("title")} description={t("description")}>
      <TallyGate data={groups}>
        {(list) => {
          const chosenCompany = company ?? list.find((g) => g.present)?.company;
          return !chosenCompany || list.length === 0 ? (
            <EmptyState icon="layers" title={t("emptyTitle")} body={t("emptyBody")} />
          ) : (
            <GroupsForm key={chosenCompany} groups={list} company={chosenCompany} />
          );
        }}
      </TallyGate>
    </Card>
  );
}

/**
 * The Tally connector (M6; ADR-0032 Proposed): status, the office PC agents (owner:
 * enrolment codes with a recent MFA sign-in, revocation) and which ledger groups the agent may
 * send (owner, principal, accountant). Behind the school's connector flag: while it is off the
 * screen says so.
 */
export function TallyConnectorScreen() {
  const t = useTranslations("tally");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const meLoaded = me !== undefined;
  const allowed = can(TALLY_PERMISSIONS);
  const status = useConnectorStatus(meLoaded && allowed);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("connector.title")}
        description={t("connector.description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("connector.title") }]}
      />
      {meLoaded && !allowed ? (
        <Alert tone="info" title={t("connector.noAccessTitle")}>
          {t("connector.noAccessBody")}
        </Alert>
      ) : (
        <TallyGate data={meLoaded ? status : { status: "loading" }}>
          {(current) => (
            <>
              <StatusCard status={current} />
              <nav aria-label={t("connector.linksLabel")} className="flex flex-wrap gap-3">
                {can(CONFIGURE) ? (
                  <Link
                    href="/settings/tally/ledgers"
                    className="font-semibold text-primary underline underline-offset-4"
                  >
                    {t("connector.toLedgers")}
                  </Link>
                ) : null}
                {can(FINANCE_READ) ? (
                  <Link
                    href="/fees"
                    className="font-semibold text-primary underline underline-offset-4"
                  >
                    {t("connector.toDues")}
                  </Link>
                ) : null}
              </nav>
              {can(DEVICE_MANAGE) && me ? <DevicesCard tenantId={me.tenant_id} /> : null}
              {can(CONFIGURE) ? <GroupsCard company={current.company} /> : null}
            </>
          )}
        </TallyGate>
      )}
    </div>
  );
}
