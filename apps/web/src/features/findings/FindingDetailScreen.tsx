"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Avatar } from "@/components/ui/Avatar";
import { Pill } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { TextAreaField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, THead, Td, Th, Tr } from "@/components/ui/Table";
import { Timeline } from "@/components/ui/Timeline";
import { Value } from "@/components/ui/Value";
import { CR_APPROVE, CR_REQUEST, ifMatch } from "@/features/change-requests/types";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { Link } from "@/i18n/navigation";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import { optionalText, text, UUID_PATTERN } from "@/lib/validation";
import {
  attributeLabel,
  DQ_KEYS,
  profileLabel,
  ruleText,
  useAttributes,
  useProfiles,
  useRules,
} from "./data";
import { FindingStatusBadge, SeverityBadge, SourceChip } from "./parts";
import { isUnresolved, SCHOOL_RECORD_ROUTE, type Bilingual, type Finding } from "./types";

const resolveSchema = z
  .object({
    note: optionalText(1000),
    // Absent when the select is disabled (no pending requests, or no access to them).
    change_request_id: z
      .string()
      .trim()
      .optional()
      .transform((value) => value ?? "")
      .refine((value) => value === "" || UUID_PATTERN.test(value), { error: "chooseOption" })
      .transform((value) => (value === "" ? null : value)),
  })
  .refine((data) => data.note !== null || data.change_request_id !== null, {
    error: "required",
    path: ["note"],
  });

/** API: 3–1000 characters (docs/09 data quality). */
const waiveSchema = z.object({ reason: text(1000, 3) });

/**
 * English and Telugu side by side: the office often explains a finding to parents. English
 * only while Telugu is switched off (ADR-0036).
 */
function BothLanguages({ text: message }: { text: Bilingual }) {
  const locale = useLocale();
  const telugu = useTeluguEnabled();
  if (!telugu) {
    return (
      <div className="space-y-1">
        <p lang="en">{message.en}</p>
      </div>
    );
  }
  const [primary, secondary] =
    locale === "te"
      ? ([
          ["te", message.te],
          ["en", message.en],
        ] as const)
      : ([
          ["en", message.en],
          ["te", message.te],
        ] as const);
  return (
    <div className="space-y-1">
      <p lang={primary[0]}>{primary[1]}</p>
      {secondary[1] && secondary[1] !== primary[1] ? (
        <p lang={secondary[0]} className="text-sm text-ink-muted">
          {secondary[1]}
        </p>
      ) : null}
    </div>
  );
}

function correctionHref(finding: Finding): string {
  const params = new URLSearchParams({ student_id: finding.student.id, finding_id: finding.id });
  if (finding.attribute_key) params.set("attribute_key", finding.attribute_key);
  return `/change-requests/new?${params.toString()}`;
}

/**
 * One finding (US-502, FR-DQ-020): explanation in English (and Telugu while it is switched
 * on, ADR-0036), the values per source (masked where sensitive), suggested corrections,
 * history, and resolve (note or change request) or waive (reason; step-up MFA, the API
 * answers 428 and the app re-authenticates).
 */
export function FindingDetailScreen({ findingId }: { findingId: string }) {
  const t = useTranslations("findings");
  const td = useTranslations("findings.detail");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const locale = useLocale();
  const api = useBffClient("staff");
  const can = useStaffCan();
  const rules = useRules();
  const attributes = useAttributes();
  const profiles = useProfiles();
  const finding = useApiQuery(DQ_KEYS.finding(findingId), () =>
    unwrap(
      api.GET("/api/v1/dq/findings/{finding_id}", {
        params: { path: { finding_id: findingId } },
      }),
    ),
  );
  const studentId = finding.status === "ready" ? finding.data.student.id : null;
  const canSeeRequests = can([CR_REQUEST, CR_APPROVE]);
  const requests = useQuery({
    queryKey: ["staff", "change-requests", "for-student", studentId],
    queryFn: async () =>
      (
        await unwrap(
          api.GET("/api/v1/change-requests", {
            params: { query: { student_id: studentId as string, limit: 50 } },
          }),
        )
      ).data,
    enabled: studentId !== null && canSeeRequests,
    retry: false,
  });

  if (finding.status === "loading") return <LoadingState label={tc("loading")} />;
  if (finding.status !== "ready") {
    return (
      <div className="space-y-6">
        <PageHeader
          breadcrumb={[{ label: t("title"), href: "/findings" }, { label: td("problem") }]}
          title={td("problem")}
        />
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {finding.status === "error" && finding.reason
            ? te(`load.${finding.reason}`)
            : tc("loadErrorBody")}
        </Alert>
      </div>
    );
  }
  const data = finding.data;
  const field = attributeLabel(attributes.data, data.attribute_key, locale);
  const open = isUnresolved(data.status);
  // DL-06 (FR-CERT-002): a blocker is resolved by hand only by someone who may waive findings,
  // after confirming it's them (the API answers 403 blocker_needs_waive / 428 otherwise). The
  // change request that corrects the record closes it without that.
  const blocker = data.severity === "blocker";
  const canResolve = can("dq.findings.resolve") && (!blocker || can("dq.findings.waive"));
  const invalidate = [DQ_KEYS.findings, DQ_KEYS.summary] as const;
  const suggestsRequest = data.routes.some((route) => route.code === SCHOOL_RECORD_ROUTE);
  const pending = (requests.data ?? []).filter(
    (item) => item.status === "pending" || item.status === "approved",
  );

  return (
    <div className="space-y-6">
      <PageHeader
        breadcrumb={[{ label: t("title"), href: "/findings" }, { label: data.rule_id }]}
        title={td("title", { rule: data.rule_id })}
        description={ruleText(rules.data, data.rule_id, locale) ?? undefined}
        badge={
          <span className="flex flex-wrap gap-2">
            <SeverityBadge severity={data.severity} />
            <FindingStatusBadge status={data.status} />
          </span>
        }
        actions={
          open ? (
            <>
              {suggestsRequest && can(CR_REQUEST) ? (
                <ButtonLink href={correctionHref(data)} variant="secondary">
                  {td("requestCorrection")}
                </ButtonLink>
              ) : null}
              {blocker && can("dq.findings.resolve") && !can("dq.findings.waive") ? (
                <p
                  className="max-w-prose text-sm text-ink-muted"
                  data-testid="blocker-resolve-hint"
                >
                  {td("blockerNeedsWaive")}
                </p>
              ) : null}
              {canResolve ? (
                <ActionDialog
                  triggerLabel={td("resolve")}
                  triggerVariant="primary"
                  title={td("resolveTitle")}
                  description={blocker ? td("blockerResolveBody") : td("resolveBody")}
                  confirmLabel={td("resolve")}
                  consequence={td("resolveConsequence")}
                  stepUp={blocker}
                  schema={resolveSchema}
                  invalidate={invalidate}
                  errorNamespace="findings"
                  submit={(input) =>
                    unwrap(
                      api.POST("/api/v1/dq/findings/{finding_id}/resolve", {
                        params: { path: { finding_id: data.id } },
                        headers: { "If-Match": ifMatch(data.version) },
                        body: input,
                      }),
                    )
                  }
                >
                  {(errors) => (
                    <>
                      <TextAreaField
                        name="note"
                        label={td("note")}
                        hint={td("noteHint")}
                        error={errors.note}
                        maxLength={1000}
                        rows={3}
                      />
                      <SelectField
                        name="change_request_id"
                        label={td("linkRequest")}
                        hint={canSeeRequests ? td("linkRequestHint") : td("linkRequestNoAccess")}
                        error={errors.change_request_id}
                        defaultValue=""
                        disabled={!canSeeRequests || pending.length === 0}
                        options={[
                          { value: "", label: td("noRequest") },
                          ...pending.map((item) => ({
                            value: item.id,
                            label: `${locale === "te" ? item.attribute_label_te : item.attribute_label_en} · ${formatDateTime(item.requested_at) ?? ""}`,
                          })),
                        ]}
                      />
                    </>
                  )}
                </ActionDialog>
              ) : null}
              {can("dq.findings.waive") ? (
                <ActionDialog
                  triggerLabel={td("waive")}
                  triggerVariant="secondary"
                  title={td("waiveTitle")}
                  description={td("waiveBody")}
                  confirmLabel={td("waive")}
                  consequence={td("waiveConsequence")}
                  confirmVariant="danger"
                  stepUp
                  schema={waiveSchema}
                  invalidate={invalidate}
                  errorNamespace="findings"
                  submit={(input) =>
                    unwrap(
                      api.POST("/api/v1/dq/findings/{finding_id}/waive", {
                        params: { path: { finding_id: data.id } },
                        headers: { "If-Match": ifMatch(data.version) },
                        body: input,
                      }),
                    )
                  }
                >
                  {(errors) => (
                    <TextAreaField
                      name="reason"
                      label={td("waiveReason")}
                      hint={td("waiveReasonHint")}
                      error={errors.reason}
                      maxLength={1000}
                      rows={3}
                    />
                  )}
                </ActionDialog>
              ) : null}
            </>
          ) : null
        }
      />

      {data.status === "needs_confirmation" ? (
        // A-01: a blocker a write without evidence removed waits for a waive holder's Resolve.
        <Alert tone="warning" title={td("needsConfirmationTitle")}>
          <span data-testid="needs-confirmation">{td("needsConfirmationBody")}</span>
        </Alert>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
        <Card title={td("whatTitle")}>
          <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-[max-content_1fr]">
            <dt className="text-sm text-ink-muted">{t("colStudent")}</dt>
            <dd className="flex flex-wrap items-center gap-2">
              {data.student.display_name ? (
                <Avatar name={data.student.display_name} size="sm" decorative />
              ) : null}
              <span className="font-semibold">
                <Value>{data.student.display_name}</Value>
              </span>
              {data.student.admission_no ? (
                <Pill variant="tag">
                  <span className="font-mono">
                    {t("admissionNo", { number: data.student.admission_no })}
                  </span>
                </Pill>
              ) : null}
            </dd>
            <dt className="text-sm text-ink-muted">{t("colField")}</dt>
            <dd>
              <Value>{field}</Value>
            </dd>
            <dt className="text-sm text-ink-muted">{td("explanation")}</dt>
            <dd>
              <BothLanguages text={data.explanation} />
            </dd>
            {data.match_explanation ? (
              <>
                <dt className="text-sm text-ink-muted">{td("matchExplanation")}</dt>
                <dd>
                  <BothLanguages text={data.match_explanation} />
                </dd>
              </>
            ) : null}
            {data.profile_key ? (
              <>
                <dt className="text-sm text-ink-muted">{t("filterProfile")}</dt>
                <dd>{profileLabel(profiles.data, data.profile_key, locale)}</dd>
              </>
            ) : null}
          </dl>
        </Card>

        <Card title={td("routesTitle")} description={td("routesBody")}>
          {data.routes.length === 0 ? (
            <p className="text-sm text-ink-muted">{td("noRoutes")}</p>
          ) : (
            <ol className="space-y-3">
              {data.routes.map((route, position) => (
                <li key={route.code} className="flex gap-3">
                  <span
                    aria-hidden="true"
                    className="flex size-7 shrink-0 items-center justify-center rounded-full bg-action text-xs font-semibold text-on-action"
                  >
                    {position + 1}
                  </span>
                  <BothLanguages text={route} />
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>

      <Card title={td("valuesTitle")} description={td("valuesBody")}>
        {data.values.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("noValues")}</p>
        ) : (
          <TableScroll label={tc("scrollableTable", { caption: td("valuesTitle") })}>
            <Table>
              <caption className="sr-only">{td("valuesTitle")}</caption>
              <THead>
                <Tr>
                  <Th>{td("colSource")}</Th>
                  <Th>{t("colField")}</Th>
                  <Th>{td("colValue")}</Th>
                </Tr>
              </THead>
              <TBody>
                {data.values.map((item) => (
                  <Tr key={item.value_id}>
                    <Td>
                      <SourceChip source={item.source} />
                    </Td>
                    <Td>{attributeLabel(attributes.data, item.attribute_key, locale)}</Td>
                    <Td>
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-mono">
                          {item.value ?? item.masked ?? t("noValue")}
                        </span>
                        {item.sensitive || item.value === null ? (
                          <Pill variant="tag">
                            <Icon name="lock" className="size-3" />
                            {t("maskedNote")}
                          </Pill>
                        ) : null}
                      </span>
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </TableScroll>
        )}
      </Card>

      <Card title={td("historyTitle")}>
        <Timeline
          label={td("timelineLabel")}
          items={[
            {
              id: "first",
              title: td("firstSeen"),
              time: <Value>{formatDateTime(data.first_seen_at)}</Value>,
              status: "done",
            },
            ...(data.reopened_count > 0
              ? [
                  {
                    id: "reopened",
                    title: td("reopened"),
                    body: td("reopenedCount", { count: data.reopened_count }),
                    status: "done" as const,
                  },
                ]
              : []),
            {
              id: "last",
              title: td("lastSeen"),
              time: <Value>{formatDateTime(data.last_seen_at)}</Value>,
              status: "done",
            },
            ...(data.status === "resolved"
              ? [
                  {
                    id: "resolved",
                    title: td("resolvedAt"),
                    time: <Value>{formatDateTime(data.resolved_at)}</Value>,
                    status: "done" as const,
                    body:
                      data.resolution_note || data.change_request_id ? (
                        <dl className="space-y-2">
                          {data.resolution_note ? (
                            <div>
                              <dt className="font-semibold text-ink">{td("note")}</dt>
                              <dd className="whitespace-pre-line">{data.resolution_note}</dd>
                            </div>
                          ) : null}
                          {data.change_request_id ? (
                            <div>
                              <dt className="font-semibold text-ink">{td("linkedRequest")}</dt>
                              <dd>
                                <Link
                                  href={`/change-requests/${data.change_request_id}`}
                                  className="text-primary underline"
                                >
                                  {td("openRequest")}
                                </Link>
                              </dd>
                            </div>
                          ) : null}
                        </dl>
                      ) : undefined,
                  },
                ]
              : []),
            ...(data.status === "waived"
              ? [
                  {
                    id: "waived",
                    title: td("waivedAt"),
                    time: <Value>{formatDateTime(data.waived_at)}</Value>,
                    status: "done" as const,
                    body: (
                      <dl>
                        <dt className="font-semibold text-ink">{td("waiveReason")}</dt>
                        <dd className="whitespace-pre-line">
                          <Value>{data.waived_reason}</Value>
                        </dd>
                      </dl>
                    ),
                  },
                ]
              : []),
            ...(open
              ? [
                  {
                    id: "open",
                    title: td("stillOpen"),
                    status: "current" as const,
                    chips: <FindingStatusBadge status={data.status} />,
                  },
                ]
              : []),
          ]}
        />
        {!open ? <p className="mt-4 text-sm text-ink-muted">{td("reopenNote")}</p> : null}
      </Card>

      <nav aria-label={td("relatedLabel")} className="flex flex-wrap gap-2" data-print="hide">
        <ButtonLink href={`/findings?student_id=${data.student.id}`} variant="secondary" size="sm">
          {td("allForStudent")}
        </ButtonLink>
        <ButtonLink href="/findings" variant="ghost" size="sm">
          {td("backToList")}
        </ButtonLink>
      </nav>
    </div>
  );
}
