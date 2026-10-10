"use client";

import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import { attributeLabel, useAttributes } from "@/features/findings/data";
import { FindingStatusBadge } from "@/features/findings/parts";
import { isSourceKey, pick } from "@/features/findings/types";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { useStudentReadiness } from "./data";
import { DiffView, OwnerBadge, ReadinessBadge } from "./parts";
import {
  slipHref,
  type ReadinessDetail,
  type ReadinessFieldDetail,
  type ReadinessItem,
  type ReadinessValue,
} from "./types";

function useSourceName() {
  const t = useTranslations("findings.sources");
  return (source: string | null | undefined) =>
    source ? (isSourceKey(source) ? t(source) : source) : "";
}

function ValueCell({ value }: { value: ReadinessValue }) {
  const t = useTranslations("readiness");
  if (value.value !== null) return <span className="font-mono break-words">{value.value}</span>;
  if (value.masked) {
    return (
      <span className="font-mono text-ink-muted">
        {value.masked}
        <span className="sr-only"> ({t("hiddenValue")})</span>
      </span>
    );
  }
  return <span className="text-ink-muted">{t("notRecorded")}</span>;
}

function ItemView({ item }: { item: ReadinessItem }) {
  const t = useTranslations("readiness");
  const locale = useLocale() as Locale;
  const sourceName = useSourceName();
  return (
    <li className="space-y-2 rounded-lg border border-border-soft p-3">
      <div className="flex flex-wrap items-center gap-2">
        <OwnerBadge owner={item.owner} />
        {item.advisory ? <Badge tone="neutral">{t("advisory")}</Badge> : null}
        {item.waived ? <Badge tone="neutral">{t("waived")}</Badge> : null}
        {item.finding_status ? <FindingStatusBadge status={item.finding_status} /> : null}
      </div>
      <p lang={locale}>{pick(item.explanation, locale)}</p>
      {item.kinds_text.en ? (
        <p className="text-sm">
          <span className="font-semibold">{t("whatDiffers")}: </span>
          {pick(item.kinds_text, locale)}
        </p>
      ) : null}
      {item.segments && item.source && item.against ? (
        <DiffView
          segments={item.segments}
          referenceLabel={sourceName(item.against)}
          otherLabel={sourceName(item.source)}
        />
      ) : null}
      {item.changes && item.changes.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-sm">
          {item.changes.map((change, index) => (
            <li key={`${change.code}-${index}`}>{pick(change, locale)}</li>
          ))}
        </ul>
      ) : null}
      <p className="text-sm text-ink-muted">{pick(item.owner_label, locale)}</p>
      {item.finding_id ? (
        <Link
          href={`/findings/${item.finding_id}`}
          className="text-sm font-semibold text-primary underline print:hidden"
        >
          {t("openFinding")}
        </Link>
      ) : null}
    </li>
  );
}

function FieldCard({ field }: { field: ReadinessFieldDetail }) {
  const t = useTranslations("readiness");
  const locale = useLocale() as Locale;
  const attributes = useAttributes();
  const sourceName = useSourceName();
  const label = attributeLabel(attributes.data, field.attribute_key, locale);
  const wrong = new Set(field.items.filter((i) => !i.advisory).map((i) => i.source));
  return (
    <Card
      title={label}
      headingLevel={3}
      actions={
        field.items.length === 0 ? (
          <Badge tone="success">{t("fieldMatches")}</Badge>
        ) : (
          <Badge tone="warning">{t("fieldDiffers", { count: field.items.length })}</Badge>
        )
      }
    >
      <table className="w-full text-sm">
        <caption className="sr-only">{t("valuesCaption", { field: label ?? "" })}</caption>
        <thead>
          <tr className="text-left text-ink-muted">
            <th scope="col" className="py-1 pr-4 font-semibold">
              {t("colRecord")}
            </th>
            <th scope="col" className="py-1 pr-4 font-semibold">
              {t("colValue")}
            </th>
            <th scope="col" className="py-1 font-semibold">
              <span className="sr-only">{t("colNote")}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {field.values.map((value) => (
            <tr key={value.source} className="border-t border-border-soft">
              <th scope="row" className="py-1.5 pr-4 text-left font-normal">
                {sourceName(value.source)}
              </th>
              <td className="py-1.5 pr-4">
                <ValueCell value={value} />
              </td>
              <td className="py-1.5">
                {field.reference === value.source ? (
                  <Badge tone="success">{t("rightValue")}</Badge>
                ) : wrong.has(value.source) ? (
                  <Badge tone="danger">{t("toCorrect")}</Badge>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {field.items.length > 0 ? (
        <ul className="mt-3 space-y-3">
          {field.items.map((item, index) => (
            <ItemView key={`${item.reason}-${item.source ?? "all"}-${index}`} item={item} />
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

function Detail({ data, profileKey }: { data: ReadinessDetail; profileKey: string }) {
  const t = useTranslations("readiness");
  const tf = useTranslations("findings");
  const locale = useLocale() as Locale;
  const profile =
    locale === "te" && data.profile.label_te ? data.profile.label_te : data.profile.label_en;
  return (
    <div className="space-y-6">
      <PageHeader
        title={<Value>{data.student.display_name}</Value>}
        description={t("studentDescription", { profile })}
        badge={<ReadinessBadge status={data.status} />}
        breadcrumb={[
          { label: tf("title"), href: "/findings" },
          { label: t("title"), href: `/findings/readiness?profile=${profileKey}` },
          { label: data.student.display_name ?? t("student") },
        ]}
        actions={
          <a
            href={slipHref(profileKey, { studentId: data.student.id })}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-10 items-center rounded-lg border border-border-soft px-4 text-sm font-semibold text-primary print:hidden"
          >
            {t("printSlip")}
          </a>
        }
      />
      {data.student.admission_no ? (
        <p className="font-mono text-sm text-ink-muted">
          {tf("admissionNo", { number: data.student.admission_no })}
        </p>
      ) : null}
      {!data.applies ? <Alert tone="info">{t("notApplicable")}</Alert> : null}
      {!data.values_shown ? <Alert tone="info">{t("valuesHidden")}</Alert> : null}
      <div className="grid gap-4 xl:grid-cols-2">
        {data.fields.map((field) => (
          <FieldCard key={field.attribute_key} field={field} />
        ))}
      </div>
    </div>
  );
}

/**
 * One student's readiness (US-504): the value every record holds, the exact difference
 * character by character (when you may see both values), and who must fix it.
 */
export function ReadinessStudentScreen({
  profileKey,
  studentId,
}: {
  profileKey: string;
  studentId: string;
}) {
  const tc = useTranslations("common");
  const query = useStudentReadiness(profileKey, studentId);
  if (query.isPending) return <LoadingState label={tc("loading")} rows={4} />;
  if (query.isError) return <ApiErrorAlert error={query.error} />;
  return <Detail data={query.data} profileKey={profileKey} />;
}
