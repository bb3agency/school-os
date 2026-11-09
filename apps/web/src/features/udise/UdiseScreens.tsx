"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Link, useRouter } from "@/i18n/navigation";
import { toLoadable, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import * as z from "@/lib/zod";
import {
  UDISE_KEYS,
  UDISE_MANAGE,
  UDISE_PRINT,
  UDISE_PROFILES,
  letterHref,
  udiseApi,
  type UdiseGroup,
  type UdiseLine,
  type UdiseLineState,
  type UdiseRequest,
  type UdiseRequestDetail,
  type UdiseRequestStatus,
} from "./api";

const STATUS_VARIANTS: Record<UdiseRequestStatus, PillVariant> = {
  prepared: "review",
  sent: "progress",
  done: "done",
  rejected: "negative",
};

const STATE_VARIANTS: Record<UdiseLineState, PillVariant> = {
  open: "review",
  fixed: "done",
  unavailable: "tag",
};

function StatusPill({ status }: { status: UdiseRequestStatus }) {
  const t = useTranslations("udise.status");
  return <Pill variant={STATUS_VARIANTS[status]}>{t(status)}</Pill>;
}

function useHeader(title: string, crumbs: { label: string; href?: string }[]) {
  const tn = useTranslations("school.nav");
  return [{ label: tn("home"), href: "/" }, ...crumbs, { label: title }];
}

/** One field group (names, dates of birth, gender) as a table of lines. */
function GroupTable({ group, showState }: { group: UdiseGroup; showState: boolean }) {
  const t = useTranslations("udise");
  if (group.lines.length === 0) return null;
  const columns: ColumnV2<UdiseLine>[] = [
    {
      key: "student",
      stack: "title",
      header: t("colStudent"),
      cell: (line) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold break-words text-ink">
            {line.student.display_name ?? t("notShown")}
          </span>
          <span className="text-xs text-ink-muted">
            {[line.class_section, line.student.admission_no].filter(Boolean).join(" · ")}
          </span>
        </span>
      ),
    },
    {
      key: "pen",
      header: t("colPen"),
      cell: (line) => <span className="num text-sm">{line.pen ?? "—"}</span>,
    },
    {
      key: "field",
      header: t("colField"),
      cell: (line) => line.field_label,
    },
    {
      key: "values",
      header: t("colValues"),
      cell: (line) => (
        <span className="flex flex-col gap-0.5 text-sm">
          <span className="break-words">
            <span className="text-ink-muted">{t("inUdise")} </span>
            {line.udise_value ?? t("blank")}
          </span>
          <span className="font-semibold break-words text-ink">
            <span className="font-normal text-ink-muted">{t("correct")} </span>
            {line.correct_value ?? "—"}
          </span>
        </span>
      ),
    },
    {
      key: "evidence",
      header: t("colEvidence"),
      cell: (line) => (
        <span className="flex flex-col gap-0.5 text-sm">
          <span>{line.correct_source}</span>
          {line.evidence.length > 0 ? (
            <span className="text-ink-muted">{line.evidence.join(", ")}</span>
          ) : null}
          <span className="text-xs text-ink-muted">{t(`aadhaar.${line.matches_aadhaar}`)}</span>
        </span>
      ),
    },
    {
      key: "state",
      header: showState ? t("colState") : t("colRequested"),
      cell: (line) =>
        showState ? (
          <Pill variant={STATE_VARIANTS[line.state]}>{t(`lineState.${line.state}`)}</Pill>
        ) : line.requested_in ? (
          <Link
            href={`/findings/udise-corrections/${line.requested_in}`}
            className="text-sm underline"
          >
            {t("alreadyRequested")}
          </Link>
        ) : (
          <span className="text-sm text-ink-muted">{t("notRequested")}</span>
        ),
    },
  ];
  return (
    <section aria-labelledby={`udise-group-${group.key}`} className="space-y-2">
      <h2 id={`udise-group-${group.key}`} className="text-base font-semibold text-ink">
        {group.label} <span className="font-normal text-ink-muted">({group.lines.length})</span>
      </h2>
      <DataTableV2
        id={`udise-${group.key}`}
        stacked
        caption={group.label}
        captionHidden
        columns={columns}
        state={toLoadable({ data: group.lines, isPending: false, isError: false, error: null })}
        rowKey={(line) => `${line.student.id}:${line.attribute_key}`}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </section>
  );
}

/**
 * FO-02 (FR-UDI-001..003, FR-UDI-006): the UDISE+ differences the MEO office must correct, and
 * the school's requests. Office staff prepare a request; the principal prints the letter.
 */
export function UdiseCorrectionsScreen() {
  const t = useTranslations("udise");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const router = useRouter();
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(UDISE_MANAGE);
  const [profile, setProfile] = useState<string>(UDISE_PROFILES[0]);
  const candidates = useQuery({
    queryKey: UDISE_KEYS.candidates(profile),
    queryFn: () => udiseApi.candidates(api, profile),
    enabled: allowed,
    retry: false,
  });
  const requests = useQuery({
    queryKey: UDISE_KEYS.list,
    queryFn: () => udiseApi.list(api),
    enabled: allowed,
    retry: false,
  });
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={useHeader(t("title"), [
        { label: t("readinessCrumb"), href: "/findings/readiness" },
      ])}
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
  const data = candidates.data;
  const toAsk = data ? data.lines_count - data.already_requested : 0;
  const requestColumns: ColumnV2<UdiseRequest>[] = [
    {
      key: "prepared",
      stack: "title",
      header: t("colPrepared"),
      cell: (row) => (
        <Link href={`/findings/udise-corrections/${row.id}`} className="font-semibold underline">
          {formatDate(row.prepared_at) ?? row.prepared_at}
        </Link>
      ),
    },
    {
      key: "lines",
      header: t("colLines"),
      cell: (row) => (
        <span className="num text-sm">
          {t("linesFor", { lines: row.lines_count, students: row.students_count })}
        </span>
      ),
    },
    {
      key: "sent",
      header: t("colSent"),
      cell: (row) =>
        row.sent_on ? (
          <span className="text-sm">
            {t("sentTo", {
              date: formatDate(row.sent_on) ?? row.sent_on,
              to: row.sent_to_label ?? "",
            })}
          </span>
        ) : (
          <span className="text-sm text-ink-muted">{t("notSent")}</span>
        ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <StatusPill status={row.status} />,
    },
  ];
  return (
    <div className="space-y-5">
      {header}
      <Alert tone="info" title={t("aboutTitle")}>
        {t("aboutBody")}
      </Alert>
      <div className="flex flex-wrap items-end gap-4">
        <SelectField
          label={t("profile")}
          name="profile"
          value={profile}
          onChange={(event) => setProfile(event.target.value)}
          options={UDISE_PROFILES.map((key) => ({ value: key, label: t(`profiles.${key}`) }))}
        />
        <ActionDialog
          triggerLabel={t("prepare.trigger", { count: toAsk })}
          triggerVariant="primary"
          triggerDisabled={toAsk === 0}
          title={t("prepare.title")}
          description={t("prepare.description", { count: toAsk })}
          confirmLabel={t("prepare.confirm")}
          consequence={t("prepare.consequence")}
          schema={z.object({})}
          invalidate={[UDISE_KEYS.all]}
          errorNamespace="udise"
          submit={(_data, key) => udiseApi.prepare(api, profile, key)}
          onSuccess={(made) => router.push(`/findings/udise-corrections/${made.id}`)}
        />
      </div>
      {data && !data.verified ? (
        <Alert tone="warning" title={t("unverifiedTitle")}>
          {t("unverifiedBody")}
        </Alert>
      ) : null}
      <ApiErrorAlert error={candidates.error} namespace="udise" />
      {candidates.isPending ? <LoadingState label={tc("loading")} /> : null}
      {data ? (
        data.lines_count === 0 ? (
          <Alert tone="success" title={t("noneTitle")}>
            {t("noneBody")}
          </Alert>
        ) : (
          <div className="space-y-5">
            <p className="text-sm text-ink-muted">
              {t("summary", {
                lines: data.lines_count,
                students: data.students_count,
                requested: data.already_requested,
              })}
            </p>
            {data.groups.map((group) => (
              <GroupTable key={group.key} group={group} showState={false} />
            ))}
          </div>
        )
      ) : null}
      <section aria-labelledby="udise-requests" className="space-y-2">
        <h2 id="udise-requests" className="text-base font-semibold text-ink">
          {t("requestsTitle")}
        </h2>
        <DataTableV2
          id="udise-requests-table"
          stacked
          caption={t("requestsTitle")}
          captionHidden
          columns={requestColumns}
          state={toLoadable({ ...requests, data: requests.data ?? [] })}
          rowKey={(row) => row.id}
          emptyTitle={t("noRequestsTitle")}
          emptyBody={t("noRequestsBody")}
          onRetry={() => void requests.refetch()}
        />
      </section>
    </div>
  );
}

/** The print button: checks the sign-in first (export.portal, step-up), then a real link. */
function LetterLink({ request }: { request: UdiseRequestDetail }) {
  const t = useTranslations("udise.letter");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const [pending, setPending] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  if (!can(UDISE_PRINT)) {
    return <p className="text-sm text-ink-muted">{t("askPrincipal")}</p>;
  }
  async function prepare() {
    setPending(true);
    setError(undefined);
    try {
      await udiseApi.checkLetter(api, request.id);
      setReady(true);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return (
    <span className="flex flex-col gap-2">
      {ready ? (
        <a
          href={letterHref(request.id)}
          target="_blank"
          rel="noopener noreferrer"
          className={buttonClasses("primary", "md")}
        >
          <Icon name="file" className="size-4" />
          {t("open")}
          <span className="sr-only"> ({t("newTab")})</span>
        </a>
      ) : (
        <Button
          onClick={() => void prepare()}
          disabled={pending || request.open_count === 0}
          variant="secondary"
        >
          {pending ? t("checking") : t("prepare")}
        </Button>
      )}
      <ApiErrorAlert error={error} namespace="udise" />
    </span>
  );
}

/** FO-02 (FR-UDI-004..008): one request, its lines now, and its follow-up. */
export function UdiseRequestScreen({ requestId }: { requestId: string }) {
  const t = useTranslations("udise");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(UDISE_MANAGE);
  const detail = useQuery({
    queryKey: UDISE_KEYS.detail(requestId),
    queryFn: () => udiseApi.detail(api, requestId),
    enabled: allowed,
    retry: false,
  });
  const title = t("detail.title");
  const breadcrumb = useHeader(title, [{ label: t("title"), href: "/findings/udise-corrections" }]);
  const request = detail.data;
  const today = new Date().toISOString().slice(0, 10);
  const header = (
    <PageHeader
      title={title}
      description={
        request
          ? t("linesFor", { lines: request.lines_count, students: request.students_count })
          : undefined
      }
      badge={request ? <StatusPill status={request.status} /> : undefined}
      breadcrumb={breadcrumb}
    />
  );
  if (!meLoaded || (allowed && detail.isPending)) {
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
  if (!request) {
    return (
      <div className="space-y-5">
        {header}
        <ApiErrorAlert error={detail.error} namespace="udise" />
      </div>
    );
  }
  return (
    <div className="space-y-5">
      {header}
      <p className="text-sm text-ink-muted">
        {t("detail.progress", { fixed: request.fixed_count, open: request.open_count })}
      </p>
      {request.sent_on ? (
        <p className="text-sm">
          {t("sentTo", {
            date: formatDate(request.sent_on) ?? request.sent_on,
            to: request.sent_to_label ?? "",
          })}
        </p>
      ) : null}
      <div className="flex flex-wrap items-start gap-3">
        <LetterLink request={request} />
        {request.status === "prepared" ? (
          <ActionDialog
            triggerLabel={t("sent.trigger")}
            title={t("sent.title")}
            description={t("sent.description")}
            confirmLabel={t("sent.confirm")}
            schema={z.object({
              sent_on: z.string().min(1, { error: "required" }),
              sent_to: z.string().min(1, { error: "chooseOption" }),
            })}
            invalidate={[UDISE_KEYS.all]}
            errorNamespace="udise"
            errorSummary
            submit={(data) => udiseApi.markSent(api, request, data.sent_on, data.sent_to)}
          >
            {(errors) => (
              <div className="space-y-4">
                <TextField
                  label={t("sent.date")}
                  name="sent_on"
                  type="date"
                  max={today}
                  defaultValue={today}
                  error={errors.sent_on}
                  required
                />
                <SelectField
                  label={t("sent.to")}
                  hint={t("sent.toHint")}
                  name="sent_to"
                  placeholder={t("sent.choose")}
                  options={request.recipients.map((r) => ({ value: r.key, label: r.label }))}
                  error={errors.sent_to}
                  required
                />
              </div>
            )}
          </ActionDialog>
        ) : null}
        {request.status === "sent"
          ? (["done", "rejected"] as const).map((outcome) => (
              <ActionDialog
                key={outcome}
                triggerLabel={t(`close.${outcome}.trigger`)}
                triggerVariant={outcome === "done" ? "primary" : "secondary"}
                title={t(`close.${outcome}.title`)}
                description={t("close.description", {
                  fixed: request.fixed_count,
                  open: request.open_count,
                })}
                confirmLabel={t(`close.${outcome}.trigger`)}
                schema={z.object({})}
                invalidate={[UDISE_KEYS.all]}
                errorNamespace="udise"
                submit={() => udiseApi.close(api, request, outcome)}
              />
            ))
          : null}
      </div>
      {!request.verified ? (
        <Alert tone="warning" title={t("unverifiedTitle")}>
          {t("unverifiedBody")}
        </Alert>
      ) : null}
      {request.groups.map((group) => (
        <GroupTable key={group.key} group={group} showState />
      ))}
    </div>
  );
}
