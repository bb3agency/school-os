"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatCount, formatDateTime } from "@/lib/format";
import { formList } from "@/lib/forms";
import { PeopleCounts } from "./BreachesScreen";
import { CategoryChecklist, CountFields } from "./fields";
import { BreachStatusPill, DutyClockPill, OriginPill, useCategoryNames } from "./parts";
import { breachFactsSchema, closeSchema, dutyDoneSchema, istLocalInput } from "./schema";
import {
  BREACH_KEYS,
  BREACH_MANAGE,
  DRAFT_KINDS,
  DUTY_KEYS,
  DUTY_METHODS,
  NOTICE_DRAFT,
  dutyClock,
  ifMatch,
  type BreachDetail,
  type BreachUpdate,
  type DraftKind,
  type Duty,
  type DutyIn,
  type DutyKey,
} from "./types";

/** Which draft each duty uses (ADR-0050): the Board outline for both Board duties. */
const TEMPLATE: Record<DutyKey, DraftKind> = {
  board_notice: "board_outline",
  board_report: "board_outline",
  people_notified: "family_notice",
};

/**
 * One breach (US-2702, FR-DPDP-031..035, ADR-0050): the facts, the three duties with their
 * clocks (Board notice, Board report, people affected told), when and how each was done, the
 * two drafts filled from the record (family notice, Board outline), turning the family notice
 * into a parent notice draft (never sent by SchoolOS itself), and closing with a reason when a
 * duty was not recorded. Changes need `privacy.breach.manage` and a fresh MFA sign-in.
 */
export function BreachDetailScreen({ breachId }: { breachId: string }) {
  const t = useTranslations("breaches");
  const td = useTranslations("breaches.detail");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const [openDraft, setOpenDraft] = useState<DraftKind | null>(null);
  const breach = useQuery({
    queryKey: BREACH_KEYS.detail(breachId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/privacy/breaches/{breach_id}", {
          params: { path: { breach_id: breachId } },
        }),
      ),
    retry: false,
  });
  const crumbs = [
    { label: tn("home"), href: "/" },
    { label: t("title"), href: "/privacy/breaches" },
    { label: td("crumb") },
  ];

  if (breach.isPending) return <LoadingState label={tc("loading")} />;
  if (!breach.data) {
    return (
      <div className="space-y-5">
        <PageHeader breadcrumb={crumbs} title={td("crumb")} />
        <ApiErrorAlert error={breach.error} namespace="breaches" />
      </div>
    );
  }
  const data = breach.data;
  const closed = data.status === "closed";
  const manage = can(BREACH_MANAGE) && !closed;
  // While a draft is open, printing gives just that draft (docs/17: print is first-class).
  const hideInPrint = openDraft ? "hide" : undefined;

  return (
    <div className="space-y-5">
      <PageHeader
        breadcrumb={crumbs}
        title={td("title", { when: formatDateTime(data.noticed_at) ?? "" })}
        badge={<BreachStatusPill status={data.status} />}
        description={<OriginPill origin={data.origin} />}
        actions={
          manage ? (
            <>
              <EditFactsDialog breach={data} />
              <CloseDialog breach={data} />
            </>
          ) : undefined
        }
      />
      {closed ? (
        <Alert
          tone="info"
          title={td("closedTitle", { when: formatDateTime(data.closed_at) ?? "" })}
        >
          {data.close_reason ? td("closedReason", { reason: data.close_reason }) : td("closedBody")}
        </Alert>
      ) : null}
      {data.processor_facts ? (
        <div data-print={hideInPrint}>
          <ProcessorFacts breach={data} />
        </div>
      ) : null}
      <div data-print={hideInPrint} className="space-y-5">
        <Facts breach={data} />
        <Duties breach={data} manage={manage} />
      </div>
      <Drafts
        breach={data}
        manage={manage && can(NOTICE_DRAFT)}
        open={openDraft}
        onOpen={setOpenDraft}
      />
    </div>
  );
}

function ProcessorFacts({ breach }: { breach: BreachDetail }) {
  const t = useTranslations("breaches.detail");
  const locale = useLocale() as Locale;
  const facts = breach.processor_facts;
  if (!facts) return null;
  return (
    <Alert tone="warning" title={t("processorTitle")}>
      <p>{t("processorBody")}</p>
      <dl className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2">
        <div className="flex flex-wrap gap-1.5">
          <dt className="font-semibold">{t("processorSeverity")}</dt>
          <dd>
            <Value>{facts.severity}</Value>
          </dd>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <dt className="font-semibold">{t("processorPeople")}</dt>
          <dd className="num">
            <Value>{formatCount(facts.approx_people ?? null, locale)}</Value>
          </dd>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <dt className="font-semibold">{t("processorOpened")}</dt>
          <dd className="num">
            <Value>{formatDateTime(facts.opened_at ?? null)}</Value>
          </dd>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <dt className="font-semibold">{t("processorWindow")}</dt>
          <dd className="num">
            {facts.window_from || facts.window_to
              ? t("window", {
                  from: formatDateTime(facts.window_from ?? null) ?? "—",
                  to: formatDateTime(facts.window_to ?? null) ?? "—",
                })
              : t("windowUnknown")}
          </dd>
        </div>
      </dl>
    </Alert>
  );
}

function Facts({ breach }: { breach: BreachDetail }) {
  const t = useTranslations("breaches.detail");
  const locale = useLocale() as Locale;
  const categoryNames = useCategoryNames();
  const rows = [
    {
      id: "description",
      label: t("description"),
      value: (
        <span className="whitespace-pre-wrap">{breach.description ?? t("descriptionHidden")}</span>
      ),
    },
    { id: "categories", label: t("categories"), value: categoryNames(breach.categories) },
    { id: "people", label: t("people"), value: <PeopleCounts row={breach} /> },
    {
      id: "containment",
      label: t("containment"),
      value: breach.containment ? (
        <span className="whitespace-pre-wrap">{breach.containment}</span>
      ) : (
        <span className="text-ink-muted">{t("containmentNone")}</span>
      ),
    },
    {
      id: "affected",
      label: t("affected"),
      value: (
        <span className="num">
          {t("affectedCounts", {
            students: formatCount(breach.affected.students, locale) ?? "0",
            guardians: formatCount(breach.affected.guardians, locale) ?? "0",
            staff: formatCount(breach.affected.staff, locale) ?? "0",
          })}
        </span>
      ),
    },
    {
      id: "recorded",
      label: t("recorded"),
      value: (
        <span>
          <span className="num">{formatDateTime(breach.created_at)}</span>
          {breach.recorded_by_name ? <> · {breach.recorded_by_name}</> : null}
        </span>
      ),
    },
  ];
  return (
    <Card title={t("factsTitle")} description={t("factsDescription")}>
      <dl className="divide-y divide-border">
        {rows.map((row) => (
          <div key={row.id} className="grid gap-1 py-3 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-4">
            <dt className="text-sm font-semibold text-ink">{row.label}</dt>
            <dd className="min-w-0 text-sm text-ink break-words">{row.value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function Duties({ breach, manage }: { breach: BreachDetail; manage: boolean }) {
  const t = useTranslations("breaches.detail");
  const byKey = new Map(breach.duties.map((duty) => [duty.key, duty] as const));
  return (
    <Card title={t("dutiesTitle")} description={t("dutiesDescription")}>
      <ol className="divide-y divide-border">
        {DUTY_KEYS.map((key) => {
          const duty = byKey.get(key);
          return duty ? (
            <DutyRow
              key={key}
              breach={breach}
              duty={duty}
              manage={manage}
              primary={breach.next_duty?.key === key}
            />
          ) : null;
        })}
      </ol>
    </Card>
  );
}

function DutyRow({
  breach,
  duty,
  manage,
  primary,
}: {
  breach: BreachDetail;
  duty: Duty;
  manage: boolean;
  primary: boolean;
}) {
  const t = useTranslations("breaches.detail");
  const td = useTranslations("breaches.duties");
  const tm = useTranslations("breaches.methods");
  const tdr = useTranslations("breaches.drafts");
  const clock = dutyClock(duty);
  return (
    <li className="grid gap-3 py-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-start md:gap-6">
      <div className="min-w-0 space-y-1.5">
        <p className="font-semibold text-ink">{td(`${duty.key}.name`)}</p>
        <p className="text-sm text-ink-muted">{td(`${duty.key}.about`)}</p>
        <div className="flex flex-wrap items-center gap-2">
          <DutyClockPill duty={duty.key} clock={clock} />
          {duty.due_at && !duty.done_at ? (
            <span className="num text-sm text-ink-muted">
              {t("dueAt", { when: formatDateTime(duty.due_at) ?? "" })}
            </span>
          ) : null}
        </div>
        {duty.done_at && duty.method ? (
          <p className="text-sm">{t("doneHow", { method: tm(duty.method) })}</p>
        ) : null}
        {duty.default_rule ? <p className="text-xs text-ink-muted">{t("defaultRule")}</p> : null}
        <p className="text-sm text-ink-muted">
          {t("template", { draft: tdr(`${TEMPLATE[duty.key]}.name`) })}
        </p>
      </div>
      {manage && !duty.done_at ? (
        <MarkDoneDialog breach={breach} duty={duty} primary={primary} />
      ) : null}
    </li>
  );
}

function MarkDoneDialog({
  breach,
  duty,
  primary,
}: {
  breach: BreachDetail;
  duty: Duty;
  primary: boolean;
}) {
  const t = useTranslations("breaches.detail");
  const td = useTranslations("breaches.duties");
  const tm = useTranslations("breaches.methods");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const now = istLocalInput();
  return (
    <ActionDialog
      triggerLabel={t("markDone")}
      triggerVariant={primary ? "primary" : "secondary"}
      triggerSize="sm"
      triggerDescription={td(`${duty.key}.name`)}
      title={t("markDoneTitle", { duty: td(`${duty.key}.name`) })}
      description={td(`${duty.key}.about`)}
      confirmLabel={t("markDone")}
      consequence={t("markDoneConsequence")}
      stepUp
      schema={dutyDoneSchema}
      invalidate={[BREACH_KEYS.all]}
      errorNamespace="breaches"
      fieldMap={(field) => field.split(".").pop()}
      submit={(data) =>
        unwrap(
          api.PATCH("/api/v1/privacy/breaches/{breach_id}", {
            params: { path: { breach_id: breach.id } },
            headers: { "If-Match": ifMatch(breach.version) },
            body: dutyBody(duty.key, { done_at: data.done_at, method: data.method }),
          }),
        )
      }
    >
      {(errors) => (
        <>
          <TextField
            name="done_at"
            type="datetime-local"
            label={t("doneAt")}
            hint={tc("istHint")}
            error={errors.done_at}
            defaultValue={now}
            max={now}
            className="max-w-xs"
          />
          <SelectField
            name="method"
            label={t("method")}
            error={errors.method}
            defaultValue=""
            placeholder={tc("chooseOne")}
            options={DUTY_METHODS.map((value) => ({ value, label: tm(value) }))}
          />
        </>
      )}
    </ActionDialog>
  );
}

/** A PATCH body that records one duty as done. */
function dutyBody(key: DutyKey, done: DutyIn): BreachUpdate {
  if (key === "board_notice") return { board_notice: done };
  if (key === "board_report") return { board_report: done };
  return { people_notified: done };
}

const factsWithStatus = breachFactsSchema.extend({
  status: z.enum(["open", "contained"], { error: "chooseOption" }),
});

function EditFactsDialog({ breach }: { breach: BreachDetail }) {
  const t = useTranslations("breaches.detail");
  const tf = useTranslations("breaches.form");
  const ts = useTranslations("breaches.status");
  const api = useBffClient("staff");
  const idFor = (field: string) => `breach-edit-${field}`;
  return (
    <ActionDialog
      triggerLabel={t("editFacts")}
      triggerVariant="secondary"
      title={t("editFactsTitle")}
      description={t("editFactsBody")}
      confirmLabel={t("saveFacts")}
      stepUp
      schema={factsWithStatus}
      extra={(form) => ({ categories: formList(form, "categories") })}
      invalidate={[BREACH_KEYS.all]}
      errorNamespace="breaches"
      submit={(data) =>
        unwrap(
          api.PATCH("/api/v1/privacy/breaches/{breach_id}", {
            params: { path: { breach_id: breach.id } },
            headers: { "If-Match": ifMatch(breach.version) },
            body: {
              description: data.description,
              categories: data.categories,
              approx_children: data.approx_children,
              approx_guardians: data.approx_guardians,
              approx_staff: data.approx_staff,
              containment: data.containment,
              status: data.status,
            },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <SelectField
            id={idFor("status")}
            name="status"
            label={t("statusLabel")}
            hint={t("statusHint")}
            error={errors.status}
            defaultValue={breach.status === "contained" ? "contained" : "open"}
            options={(["open", "contained"] as const).map((value) => ({
              value,
              label: ts(value),
            }))}
          />
          <TextAreaField
            id={idFor("description")}
            name="description"
            label={tf("descriptionLabel")}
            hint={tf("descriptionHint")}
            error={errors.description}
            defaultValue={breach.description ?? ""}
            maxLength={4000}
            rows={5}
          />
          <CategoryChecklist
            id={idFor("categories")}
            error={errors.categories}
            selected={breach.categories}
          />
          <CountFields idFor={idFor} errors={errors} values={breach} />
          <TextAreaField
            id={idFor("containment")}
            name="containment"
            label={tf("containmentLabel")}
            hint={tf("containmentHint")}
            error={errors.containment}
            defaultValue={breach.containment ?? ""}
            maxLength={4000}
            rows={4}
          />
        </>
      )}
    </ActionDialog>
  );
}

function CloseDialog({ breach }: { breach: BreachDetail }) {
  const t = useTranslations("breaches.detail");
  const td = useTranslations("breaches.duties");
  const api = useBffClient("staff");
  const open = breach.duties.filter((duty) => !duty.done_at);
  const needsReason = open.length > 0;
  return (
    <ActionDialog
      triggerLabel={t("close")}
      triggerVariant="secondary"
      title={t("closeTitle")}
      description={
        needsReason
          ? t("closeOpenDuties", { duties: open.map((duty) => td(`${duty.key}.name`)).join(", ") })
          : t("closeAllDone")
      }
      confirmLabel={t("close")}
      confirmVariant="danger"
      consequence={t("closeConsequence")}
      stepUp
      schema={closeSchema(needsReason)}
      invalidate={[BREACH_KEYS.all]}
      errorNamespace="breaches"
      submit={(data) =>
        unwrap(
          api.PATCH("/api/v1/privacy/breaches/{breach_id}", {
            params: { path: { breach_id: breach.id } },
            headers: { "If-Match": ifMatch(breach.version) },
            body: {
              status: "closed",
              ...(data.close_reason ? { close_reason: data.close_reason } : {}),
            },
          }),
        )
      }
    >
      {(errors) => (
        <TextAreaField
          name="close_reason"
          label={needsReason ? t("closeReason") : t("closeReasonOptional")}
          hint={t("closeReasonHint")}
          error={errors.close_reason}
          maxLength={500}
          rows={3}
        />
      )}
    </ActionDialog>
  );
}

function Drafts({
  breach,
  manage,
  open,
  onOpen,
}: {
  breach: BreachDetail;
  manage: boolean;
  open: DraftKind | null;
  onOpen: (draft: DraftKind | null) => void;
}) {
  const t = useTranslations("breaches.drafts");
  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-4">
        <div className="flex flex-wrap gap-2" data-print="hide">
          {DRAFT_KINDS.map((kind) => (
            <Button
              key={kind}
              variant="secondary"
              size="sm"
              aria-expanded={open === kind}
              onClick={() => onOpen(open === kind ? null : kind)}
            >
              {open === kind ? t("hide") : t(`${kind}.show`)}
            </Button>
          ))}
          {manage ? <NoticeDraftDialog breach={breach} /> : null}
        </div>
        {open ? <DraftText breachId={breach.id} kind={open} /> : null}
      </div>
    </Card>
  );
}

function DraftText({ breachId, kind }: { breachId: string; kind: DraftKind }) {
  const t = useTranslations("breaches.drafts");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [copied, setCopied] = useState(false);
  const draft = useQuery({
    queryKey: BREACH_KEYS.draft(breachId, kind),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/privacy/breaches/{breach_id}/drafts/{draft}", {
          params: { path: { breach_id: breachId, draft: kind } },
        }),
      ),
    retry: false,
  });
  if (draft.isPending) return <LoadingState label={tc("loading")} />;
  if (!draft.data) return <ApiErrorAlert error={draft.error} namespace="breaches" />;
  const text = draft.data.text;
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  return (
    <section aria-labelledby={`draft-${kind}`} className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={`draft-${kind}`} className="text-base font-semibold text-ink">
          {draft.data.title}
        </h3>
        <span className="text-xs text-ink-muted">
          {t("templateVersion", { version: draft.data.template_version })}
        </span>
      </div>
      <div className="rounded-lg border border-border bg-surface-muted p-4 text-sm whitespace-pre-wrap text-ink print:border-0 print:bg-transparent print:p-0">
        {text}
      </div>
      <p className="text-sm text-ink-muted" data-print="hide">
        {t("notSent")}
      </p>
      <div className="flex flex-wrap items-center gap-2" data-print="hide">
        <Button variant="secondary" size="sm" onClick={() => void copy()}>
          {t("copy")}
        </Button>
        <Button variant="secondary" size="sm" onClick={() => window.print()}>
          {t("print")}
        </Button>
        <span role="status" className="text-sm">
          {copied ? t("copied") : null}
        </span>
      </div>
    </section>
  );
}

function NoticeDraftDialog({ breach }: { breach: BreachDetail }) {
  const t = useTranslations("breaches.drafts");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("makeNotice")}
      triggerVariant="secondary"
      triggerSize="sm"
      title={t("makeNoticeTitle")}
      description={t("makeNoticeBody")}
      confirmLabel={t("makeNotice")}
      consequence={t("makeNoticeConsequence")}
      stepUp
      schema={z.object({})}
      errorNamespace="breaches"
      submit={() =>
        unwrap(
          api.POST("/api/v1/privacy/breaches/{breach_id}/notice-draft", {
            params: { path: { breach_id: breach.id } },
          }),
        )
      }
      renderResult={(result, close) => (
        <div className="space-y-4">
          <Alert tone="success" live title={t("noticeMadeTitle")}>
            {t("noticeMadeBody")}
          </Alert>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="secondary" onClick={close}>
              {tc("close")}
            </Button>
            <ButtonLink href={`/notices/${result.notice_id}`}>{t("openNotice")}</ButtonLink>
          </div>
        </div>
      )}
    />
  );
}
