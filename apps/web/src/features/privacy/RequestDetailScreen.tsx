"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useState, type ReactNode } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { TextAreaField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SaveBar } from "@/components/ui/SaveBar";
import { SelectField } from "@/components/ui/Select";
import { Timeline, type TimelineItem } from "@/components/ui/Timeline";
import { Value } from "@/components/ui/Value";
import { exportRefetchInterval, startDownload } from "@/features/exports/data";
import { ifMatch } from "@/features/settings/data";
import { useFormDirty } from "@/features/settings/useFormDirty";
import { Link } from "@/i18n/navigation";
import { ApiError, reasonOf, toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import * as z from "@/lib/zod";
import type { Locale } from "@/i18n/routing";
import { DueCell, OutcomePill, RequestStatusPill } from "./parts";
import {
  PRIVACY_DECIDE,
  PRIVACY_KEYS,
  PRIVACY_MANAGE,
  SETTABLE_STATUSES,
  VERIFICATION_METHODS,
  answerLetter,
  chosenCount,
  isOpen,
  letterHref,
  type ErasurePlanRow,
  type ErasureResult,
  type PrivacyRequest,
  type PrivacyRequestPatch,
} from "./types";

const NOTES_MAX = 4000;
const RESPONSE_MAX = 4000;
const emptySchema = z.object({});
const responseSchema = z.object({
  response_text: z.string().trim().max(RESPONSE_MAX, { error: "tooLong" }).default(""),
});

/**
 * One privacy request (US-2603..US-2607; FR-DPDP-005..016; docs/17 §5.10): the request, notes
 * and how the office checked who asked, the timeline, and the panel for its kind (access
 * report, linked corrections, the erasure plan, the nomination, the complaint's answer).
 * Decisions (release, erase, close access/erasure/complaint) need `privacy.request.decide`, a
 * fresh sign-in and someone other than the person who logged the request (the API checks).
 */
export function RequestDetailScreen({ requestId }: { requestId: string }) {
  const t = useTranslations("privacy.detail");
  const tr = useTranslations("privacy.requests");
  const tk = useTranslations("privacy.kinds");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: PRIVACY_KEYS.request(requestId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/privacy/requests/{request_id}", {
          params: { path: { request_id: requestId } },
        }),
      ),
    // While the access report is being made, ask again now and then.
    refetchInterval: (state) => {
      const report = state.state.data?.report;
      return exportRefetchInterval(
        report ? [report] : undefined,
        state.state.dataUpdateCount,
        state.state.status === "error",
      );
    },
    refetchIntervalInBackground: false,
    retry: false,
  });

  const crumbs = [
    { label: tn("home"), href: "/" },
    { label: tr("title"), href: "/privacy/requests" },
  ];

  if (query.isPending) {
    return (
      <div className="space-y-5">
        <PageHeader title={t("title")} breadcrumb={[...crumbs, { label: t("title") }]} />
        <LoadingState label={tc("loading")} />
      </div>
    );
  }
  if (query.isError) {
    const reason = reasonOf(query.error);
    return (
      <div className="space-y-5">
        <PageHeader title={t("title")} breadcrumb={[...crumbs, { label: t("title") }]} />
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {reason ? te(`load.${reason}`) : tc("loadErrorBody")}
        </Alert>
      </div>
    );
  }

  const request = query.data;
  const answer = answerLetter(request.kind);
  const letters = (
    <div className="flex flex-wrap gap-2" data-print="hide">
      <a
        href={letterHref(request.id, "acknowledgement")}
        target="_blank"
        rel="noopener noreferrer"
        className={buttonClasses("secondary", "sm")}
      >
        <Icon name="printer" className="size-4" />
        {t("printAcknowledgement")}
        <span className="sr-only"> ({t("newTab")})</span>
      </a>
      {answer ? (
        <a
          href={letterHref(request.id, answer)}
          target="_blank"
          rel="noopener noreferrer"
          className={buttonClasses("secondary", "sm")}
        >
          <Icon name="printer" className="size-4" />
          {t("printAnswer")}
          <span className="sr-only"> ({t("newTab")})</span>
        </a>
      ) : null}
    </div>
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title={<span className="num">{request.reference}</span>}
        badge={<RequestStatusPill status={request.status} />}
        description={tk(`${request.kind}.label`)}
        breadcrumb={[...crumbs, { label: request.reference }]}
        actions={letters}
      />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-5">
          <RequestFacts request={request} />
          <KindPanel request={request} />
          <WorkCard request={request} />
          <ClosePanel request={request} />
        </div>
        <div className="min-w-0 space-y-5">
          <Card title={t("timelineTitle")}>
            <Events request={request} />
          </Card>
        </div>
      </div>
    </div>
  );
}

function Fact({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="grid gap-1 py-2 sm:grid-cols-[12rem_1fr] sm:gap-4">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{children}</dd>
    </div>
  );
}

function RequestFacts({ request }: { request: PrivacyRequest }) {
  const t = useTranslations("privacy.detail");
  const trel = useTranslations("privacy.relationships");
  const tm = useTranslations("privacy.verification");
  return (
    <Card title={t("requestTitle")}>
      <dl className="divide-y divide-border">
        <Fact label={t("about")}>
          <Value>{request.subject_label}</Value>
        </Fact>
        <Fact label={t("askedBy")}>
          {request.requester_name ? `${request.requester_name} · ` : null}
          {trel(request.requester_relationship)}
        </Fact>
        <Fact label={t("received")}>
          <span className="num">{formatDate(request.received_on)}</span>
        </Fact>
        <Fact label={t("due")}>
          <DueCell
            dueOn={request.due_on}
            daysLeft={request.days_left}
            overdue={request.overdue}
            open={isOpen(request.status)}
          />
        </Fact>
        <Fact label={t("loggedBy")}>
          <Value>{request.logged_by_name}</Value>
        </Fact>
        <Fact label={t("checkedHow")}>
          {request.verification_method ? (
            <>
              {tm(request.verification_method)}
              {request.verified_at ? (
                <span className="num text-sm text-ink-muted">
                  {" "}
                  · {formatDateTime(request.verified_at)}
                </span>
              ) : null}
            </>
          ) : (
            <span className="text-ink-muted">{tm("notChecked")}</span>
          )}
        </Fact>
        <Fact label={t("whatTheyAsked")}>
          {request.request_text ? (
            <p className="whitespace-pre-line">{request.request_text}</p>
          ) : (
            <Value>{null}</Value>
          )}
        </Fact>
        {request.response_text ? (
          <Fact label={t("answer")}>
            <p className="whitespace-pre-line">{request.response_text}</p>
          </Fact>
        ) : null}
      </dl>
    </Card>
  );
}

function Events({ request }: { request: PrivacyRequest }) {
  const t = useTranslations("privacy.detail");
  const tev = useTranslations("privacy.events");
  if (request.events.length === 0) return <p className="text-sm text-ink-muted">{t("noEvents")}</p>;
  const items: TimelineItem[] = [...request.events]
    .sort((a, b) => a.seq - b.seq)
    .map((event) => ({
      id: String(event.seq),
      title: translateOr(tev, event.event, "other"),
      time: <span className="num">{formatDateTime(event.at)}</span>,
      body: event.actor_name ?? t("someone"),
      status: "done",
    }));
  return <Timeline label={t("timelineTitle")} items={items} />;
}

function KindPanel({ request }: { request: PrivacyRequest }) {
  switch (request.kind) {
    case "access":
      return <AccessPanel request={request} />;
    case "correction":
      return <CorrectionPanel request={request} />;
    case "erasure":
      return <ErasurePanel request={request} />;
    case "nomination":
      return <NominationPanel request={request} />;
    default:
      return null;
  }
}

/** "Someone else must release it" and friends: why a decision button is not offered. */
function DecideNote({
  request,
  action,
}: {
  request: PrivacyRequest;
  action: "release" | "erase" | "close";
}) {
  const t = useTranslations("privacy.detail");
  const can = useStaffCan();
  if (request.logged_by_me && can(PRIVACY_DECIDE)) {
    return <p className="text-sm font-semibold text-ink">{t(`someoneElse.${action}`)}</p>;
  }
  if (!can(PRIVACY_DECIDE)) return <p className="text-sm text-ink-muted">{t("decideRole")}</p>;
  return null;
}

function AccessPanel({ request }: { request: PrivacyRequest }) {
  const t = useTranslations("privacy.access");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const path = { request_id: request.id };
  const open = isOpen(request.status);
  const report = request.report;
  const busy = report?.status === "queued" || report?.status === "running";
  const released = request.report_released_at !== null;

  async function run(action: () => Promise<unknown>, refresh = true) {
    setPending(true);
    setError(undefined);
    try {
      await action();
      if (refresh) {
        await queryClient.invalidateQueries({ queryKey: PRIVACY_KEYS.request(request.id) });
      }
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const build = () =>
    run(() =>
      unwrap(api.POST("/api/v1/privacy/requests/{request_id}/report", { params: { path } })),
    );
  const download = () =>
    run(async () => {
      const link = await unwrap(
        api.GET("/api/v1/privacy/requests/{request_id}/report/download-url", {
          params: { path },
        }),
      );
      startDownload(link.url);
    }, false);

  let state: ReactNode;
  if (!report) state = <p>{t("notBuilt")}</p>;
  else if (busy) {
    state = (
      <p role="status" className="text-ink-muted">
        {t("building")}
      </p>
    );
  } else if (report.status === "failed") state = <p>{t("failed")}</p>;
  else if (report.status === "expired") state = <p>{t("expired")}</p>;
  else if (released) {
    state = (
      <p>
        {t("released", { date: formatDateTime(request.report_released_at) ?? "" })}{" "}
        {report.expires_at ? (
          <span className="num text-ink-muted">
            {t("until", { date: formatDateTime(report.expires_at) ?? "" })}
          </span>
        ) : null}
      </p>
    );
  } else state = <p>{t("ready")}</p>;

  const canBuild =
    open && can(PRIVACY_MANAGE) && !busy && (!report || report.status !== "ready" || !released);
  const releasable = open && report?.status === "ready" && !released;

  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-3">
        {state}
        {releasable && !request.verification_method ? (
          <Alert tone="warning">{t("verifyFirst")}</Alert>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          {canBuild ? (
            <Button variant="secondary" disabled={pending} onClick={() => void build()}>
              {pending ? tc("working") : report ? t("buildAgain") : t("build")}
            </Button>
          ) : null}
          {releasable && can(PRIVACY_DECIDE) ? (
            <ActionDialog
              triggerLabel={t("release")}
              triggerVariant="primary"
              triggerDisabled={!request.can_decide}
              title={t("releaseTitle")}
              description={t("releaseBody")}
              confirmLabel={t("release")}
              stepUp
              consequence={t("releaseConsequence")}
              schema={emptySchema}
              invalidate={[PRIVACY_KEYS.request(request.id)]}
              errorNamespace="privacy"
              submit={() =>
                unwrap(
                  api.POST("/api/v1/privacy/requests/{request_id}/report/release", {
                    params: { path },
                  }),
                )
              }
            />
          ) : null}
          {released && report?.status === "ready" && can(PRIVACY_DECIDE) ? (
            <Button disabled={pending} onClick={() => void download()}>
              {pending ? tc("working") : t("download")}
            </Button>
          ) : null}
        </div>
        {releasable ? <DecideNote request={request} action="release" /> : null}
        <ApiErrorAlert error={error} namespace="privacy" />
      </div>
    </Card>
  );
}

const CR_STATUSES = ["pending", "approved", "rejected", "expired", "cancelled"] as const;

function CorrectionPanel({ request }: { request: PrivacyRequest }) {
  const t = useTranslations("privacy.correction");
  const tcs = useTranslations("privacy.correction.status");
  const can = useStaffCan();
  const open = isOpen(request.status);
  const manage = can(PRIVACY_MANAGE) && open;
  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-3">
        {request.links.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("none")}</p>
        ) : (
          <ul className="space-y-2">
            {request.links.map((link) => (
              <li key={link.target_id} className="flex flex-wrap items-center gap-2">
                <Link
                  href={`/change-requests/${link.target_id}`}
                  className="font-semibold text-primary underline-offset-4 hover:underline"
                >
                  {t("changeRequest")}
                </Link>
                <span className="num text-sm text-ink-muted">{formatDate(link.created_at)}</span>
                {link.status ? (
                  <Pill variant={link.status === "pending" ? "progress" : "tag"} size="sm">
                    {translateOr(tcs, link.status, "unknown")}
                  </Pill>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {manage && request.student_id ? (
          <div className="flex flex-wrap gap-2">
            <ButtonLink
              href={`/change-requests/new?student_id=${request.student_id}`}
              variant="secondary"
            >
              {t("open")}
            </ButtonLink>
            <LinkChangeRequest request={request} studentId={request.student_id} />
          </div>
        ) : null}
        {manage && !request.student_id ? (
          <p className="text-sm text-ink-muted">{t("studentsOnly")}</p>
        ) : null}
      </div>
    </Card>
  );
}

function LinkChangeRequest({ request, studentId }: { request: PrivacyRequest; studentId: string }) {
  const t = useTranslations("privacy.correction");
  const tcs = useTranslations("privacy.correction.status");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [open, setOpen] = useState(false);
  const fieldId = useId();
  const linked = new Set(request.links.map((link) => link.target_id));
  const list = useQuery({
    queryKey: ["staff", "change-requests", "student", studentId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/change-requests", {
          params: { query: { student_id: studentId, limit: 50 } },
        }),
      ),
    enabled: open,
    retry: false,
  });
  const options = (list.data?.data ?? [])
    .filter((row) => !linked.has(row.id))
    .filter((row) => (CR_STATUSES as readonly string[]).includes(row.status))
    .map((row) => ({
      value: row.id,
      label: `${row.attribute_label_en} · ${formatDate(row.requested_at) ?? ""} · ${tcs(row.status)}`,
    }));
  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>
        {t("link")}
      </Button>
      <ActionDialog
        open={open}
        onOpenChange={setOpen}
        title={t("linkTitle")}
        description={t("linkBody")}
        confirmLabel={t("link")}
        schema={z.object({
          target_id: z.string().trim().min(1, { error: "chooseOption" }),
        })}
        invalidate={[PRIVACY_KEYS.request(request.id)]}
        errorNamespace="privacy"
        submit={(input) =>
          unwrap(
            api.POST("/api/v1/privacy/requests/{request_id}/links", {
              params: { path: { request_id: request.id } },
              body: { kind: "change_request", target_id: input.target_id },
            }),
          )
        }
      >
        {(errors) =>
          list.isError ? (
            <ApiErrorAlert error={list.error} />
          ) : (
            <SelectField
              id={fieldId}
              name="target_id"
              label={t("pick")}
              error={errors.target_id}
              defaultValue=""
              placeholder={list.isPending ? tc("loading") : tc("chooseOne")}
              options={options}
            />
          )
        }
      </ActionDialog>
    </>
  );
}

function ErasurePanel({ request }: { request: PrivacyRequest }) {
  const t = useTranslations("privacy.erasure");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const can = useStaffCan();
  const [chosen, setChosen] = useState<string[]>([]);
  const [result, setResult] = useState<ErasureResult | null>(null);
  const plan = useQuery({
    queryKey: PRIVACY_KEYS.erasurePlan(request.id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/privacy/requests/{request_id}/erasure-plan", {
          params: { path: { request_id: request.id } },
        }),
      ),
    retry: false,
  });
  const rows = plan.data?.rows ?? [];
  const open = isOpen(request.status);
  const decide = open && can(PRIVACY_DECIDE);
  const total = chosenCount(rows, chosen);
  const toggle = (key: string, on: boolean) =>
    setChosen((current) => (on ? [...current, key] : current.filter((item) => item !== key)));

  const columns: ColumnV2<ErasurePlanRow>[] = [
    ...(decide
      ? [
          {
            key: "tick",
            stack: "actions" as const,
            header: <span className="sr-only">{t("colTick")}</span>,
            cell: (row: ErasurePlanRow) =>
              row.outcome === "erasable" ? (
                <input
                  type="checkbox"
                  className="size-5 accent-primary"
                  checked={chosen.includes(row.class_key)}
                  disabled={!request.can_decide}
                  onChange={(event) => toggle(row.class_key, event.currentTarget.checked)}
                  aria-label={t("tick", { label: row.label })}
                />
              ) : null,
          },
        ]
      : []),
    { key: "label", stack: "title", header: t("colRecord"), cell: (row) => row.label },
    { key: "outcome", header: t("colOutcome"), cell: (row) => <OutcomePill row={row} /> },
    {
      key: "count",
      header: t("colCount"),
      numeric: true,
      cell: (row) => <Value>{formatCount(row.count, locale)}</Value>,
    },
    { key: "reason", stack: "wide", header: t("colReason"), cell: (row) => row.reason },
  ];

  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-4">
        {result ? (
          <Alert tone="success" live title={t("doneTitle")}>
            <ul className="list-disc ps-5">
              {result.erased.map((row) => (
                <li key={row.class_key}>
                  {rows.find((item) => item.class_key === row.class_key)?.label ?? row.class_key}:{" "}
                  <span className="num">{formatCount(row.count, locale)}</span>
                </li>
              ))}
            </ul>
            {result.manual_steps.length > 0 ? (
              <>
                <p className="mt-2 font-semibold">{t("manualSteps")}</p>
                <ul className="list-disc ps-5">
                  {result.manual_steps.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </Alert>
        ) : null}
        <DataTableV2
          id="privacy-erasure-plan"
          stacked
          caption={t("title")}
          captionHidden
          columns={columns}
          state={toLoadable({ ...plan, data: rows })}
          rowKey={(row) => row.class_key}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
          onRetry={() => void plan.refetch()}
        />
        {decide && rows.some((row) => row.outcome === "erasable") ? (
          <div className="space-y-2">
            <ActionDialog
              triggerLabel={t("erase")}
              triggerVariant="danger"
              triggerDisabled={chosen.length === 0 || !request.can_decide}
              title={t("eraseTitle")}
              description={t("eraseBody")}
              confirmLabel={t("erase")}
              confirmVariant="danger"
              stepUp
              consequence={t("consequence", { count: total, kinds: chosen.length })}
              schema={emptySchema}
              invalidate={[PRIVACY_KEYS.request(request.id), PRIVACY_KEYS.erasurePlan(request.id)]}
              errorNamespace="privacy"
              submit={() =>
                unwrap(
                  api.POST("/api/v1/privacy/requests/{request_id}/erasure", {
                    params: { path: { request_id: request.id } },
                    body: { class_keys: chosen },
                  }),
                )
              }
              onSuccess={(done) => {
                setResult(done);
                setChosen([]);
              }}
            />
            {chosen.length === 0 && request.can_decide ? (
              <p className="text-sm text-ink-muted">{t("tickFirst")}</p>
            ) : null}
            <DecideNote request={request} action="erase" />
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function NominationPanel({ request }: { request: PrivacyRequest }) {
  const t = useTranslations("privacy.nomination");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const nominee = request.nominee;
  return (
    <Card title={t("title")}>
      <div className="space-y-3">
        {nominee ? (
          <dl className="divide-y divide-border">
            <Fact label={t("name")}>
              <Value>{nominee.name}</Value>
            </Fact>
            <Fact label={t("relationship")}>
              <Value>{nominee.relationship}</Value>
            </Fact>
            <Fact label={t("contact")}>
              <Value>{nominee.contact}</Value>
            </Fact>
          </dl>
        ) : (
          <p className="text-sm text-ink-muted">{t("none")}</p>
        )}
        {nominee?.withdrawn_at ? (
          <p>{t("withdrawn", { date: formatDate(nominee.withdrawn_at) ?? "" })}</p>
        ) : nominee && can(PRIVACY_MANAGE) && isOpen(request.status) ? (
          <ActionDialog
            triggerLabel={t("withdraw")}
            triggerVariant="secondary"
            title={t("withdrawTitle")}
            confirmLabel={t("withdraw")}
            confirmVariant="danger"
            consequence={t("withdrawConsequence")}
            schema={emptySchema}
            invalidate={[PRIVACY_KEYS.request(request.id)]}
            errorNamespace="privacy"
            submit={() =>
              unwrap(
                api.PATCH("/api/v1/privacy/requests/{request_id}", {
                  params: { path: { request_id: request.id } },
                  headers: { "If-Match": ifMatch(request.version) },
                  body: { nomination_withdrawn: true },
                }),
              )
            }
          />
        ) : null}
      </div>
    </Card>
  );
}

/** Notes, where it stands and how the office checked who asked (PATCH with If-Match). */
function WorkCard({ request }: { request: PrivacyRequest }) {
  const t = useTranslations("privacy.work");
  const can = useStaffCan();
  const [generation, setGeneration] = useState(0);
  const [saved, setSaved] = useState(false);
  if (!can(PRIVACY_MANAGE) || !isOpen(request.status)) {
    return request.notes ? (
      <Card title={t("title")}>
        <p className="whitespace-pre-line">{request.notes}</p>
      </Card>
    ) : null;
  }
  return (
    <Card title={t("title")} description={t("description")}>
      <WorkForm
        key={`${request.version}:${generation}`}
        request={request}
        saved={saved}
        onSaved={setSaved}
        onDiscard={() => {
          setSaved(false);
          setGeneration((value) => value + 1);
        }}
      />
    </Card>
  );
}

/** Only what changed, so a save never overwrites someone else's field by accident. */
export function workPatch(
  request: Pick<PrivacyRequest, "status" | "verification_method" | "notes">,
  values: { status: string; verification_method: string; notes: string },
): PrivacyRequestPatch {
  const patch: PrivacyRequestPatch = {};
  const status = SETTABLE_STATUSES.find((value) => value === values.status);
  if (status && status !== request.status) patch.status = status;
  const method = VERIFICATION_METHODS.find((value) => value === values.verification_method);
  if (method && method !== request.verification_method) patch.verification_method = method;
  const notes = values.notes.trim();
  if (notes !== (request.notes ?? "")) patch.notes = notes || null;
  return patch;
}

function WorkForm({
  request,
  saved,
  onSaved,
  onDiscard,
}: {
  request: PrivacyRequest;
  saved: boolean;
  onSaved: (saved: boolean) => void;
  onDiscard: () => void;
}) {
  const t = useTranslations("privacy.work");
  const ts = useTranslations("privacy.status");
  const tm = useTranslations("privacy.verification");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const base = useId();
  const { ref, dirty, onChange } = useFormDirty();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const stale = error instanceof ApiError && error.status === 412;

  async function save(form: HTMLFormElement) {
    const data = new FormData(form);
    const value = (name: string) => {
      const item = data.get(name);
      return typeof item === "string" ? item : "";
    };
    const patch = workPatch(request, {
      status: value("status"),
      verification_method: value("verification_method"),
      notes: value("notes"),
    });
    if (Object.keys(patch).length === 0) return;
    setPending(true);
    setError(undefined);
    try {
      await unwrap(
        api.PATCH("/api/v1/privacy/requests/{request_id}", {
          params: { path: { request_id: request.id } },
          headers: { "If-Match": ifMatch(request.version) },
          body: patch,
        }),
      );
      onSaved(true);
      await queryClient.invalidateQueries({ queryKey: PRIVACY_KEYS.all });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const settable = (SETTABLE_STATUSES as readonly string[]).includes(request.status);
  return (
    <form
      ref={ref}
      noValidate
      onChange={() => {
        onSaved(false);
        onChange();
      }}
      onSubmit={(event) => {
        event.preventDefault();
        void save(event.currentTarget);
      }}
      className="space-y-4"
    >
      <div className="grid gap-4 md:grid-cols-2">
        <SelectField
          id={`${base}-status`}
          name="status"
          label={t("status")}
          defaultValue={settable ? request.status : ""}
          {...(settable ? {} : { placeholder: ts(request.status) })}
          options={SETTABLE_STATUSES.map((value) => ({ value, label: ts(value) }))}
        />
        <SelectField
          id={`${base}-verification`}
          name="verification_method"
          label={t("verification")}
          hint={t("verificationHint")}
          defaultValue={request.verification_method ?? ""}
          placeholder={tm("notChecked")}
          options={VERIFICATION_METHODS.map((value) => ({ value, label: tm(value) }))}
        />
      </div>
      <TextAreaField
        id={`${base}-notes`}
        name="notes"
        label={t("notes")}
        hint={t("notesHint")}
        defaultValue={request.notes ?? ""}
        maxLength={NOTES_MAX}
        rows={4}
      />
      <ApiErrorAlert error={error} namespace="privacy" />
      {stale ? (
        <Button variant="secondary" onClick={onDiscard}>
          {t("reload")}
        </Button>
      ) : null}
      <SaveBar dirty={dirty} savedLabel={saved ? t("saved") : undefined}>
        <Button variant="secondary" disabled={!dirty || pending} onClick={onDiscard}>
          {t("discard")}
        </Button>
        <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
          {pending ? tc("working") : t("save")}
        </Button>
      </SaveBar>
    </form>
  );
}

/**
 * Closing. A complaint needs a written answer ("Close with this answer"); access, erasure and
 * complaints are decisions (`privacy.request.decide`, not the person who logged it, step-up).
 */
function ClosePanel({ request }: { request: PrivacyRequest }) {
  const t = useTranslations("privacy.close");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const base = useId();
  if (!isOpen(request.status)) return null;
  const decision =
    request.kind === "access" || request.kind === "erasure" || request.kind === "grievance";
  const allowed = decision ? can(PRIVACY_DECIDE) : can(PRIVACY_MANAGE);
  if (!allowed && !can(PRIVACY_MANAGE)) return null;
  const grievance = request.kind === "grievance";
  const blocked = decision && !request.can_decide;
  const close = (response: string) =>
    unwrap(
      api.POST("/api/v1/privacy/requests/{request_id}/close", {
        params: { path: { request_id: request.id } },
        body: response ? { response_text: response } : {},
      }),
    );

  return (
    <Card title={grievance ? t("answerTitle") : t("title")} description={t("description")}>
      <div className="space-y-3">
        {allowed ? (
          <ActionDialog
            triggerLabel={grievance ? t("closeWithAnswer") : t("close")}
            triggerVariant={grievance ? "primary" : "secondary"}
            triggerDisabled={blocked}
            title={grievance ? t("closeWithAnswer") : t("closeTitle")}
            description={grievance ? t("answerBody") : t("closeBody")}
            confirmLabel={grievance ? t("closeWithAnswer") : t("close")}
            stepUp={decision}
            consequence={t("consequence")}
            schema={
              grievance
                ? responseSchema.refine((data) => data.response_text.length > 0, {
                    path: ["response_text"],
                    error: "required",
                  })
                : responseSchema
            }
            fieldMap={(field) => field.split(".").pop()}
            invalidate={[PRIVACY_KEYS.all]}
            errorNamespace="privacy"
            submit={(input) => close(input.response_text)}
          >
            {(errors) => (
              <TextAreaField
                id={`${base}-response`}
                name="response_text"
                label={grievance ? t("answer") : t("answerOptional")}
                hint={t("answerHint")}
                error={errors.response_text}
                defaultValue={request.response_text ?? ""}
                maxLength={RESPONSE_MAX}
                rows={5}
              />
            )}
          </ActionDialog>
        ) : null}
        {decision ? <DecideNote request={request} action="close" /> : null}
      </div>
    </Card>
  );
}
