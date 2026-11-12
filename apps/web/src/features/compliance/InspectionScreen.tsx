"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useMemo, useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { Link } from "@/i18n/navigation";
import {
  newIdempotencyKey,
  toLoadable,
  unwrap,
  useApiMutation,
  useBffClient,
} from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate, formatDateTime } from "@/lib/format";
import { COMPLIANCE_MANAGE, COMPLIANCE_READ } from "./data";
import {
  PACKS_KEYS,
  REGISTER_READ,
  fetchPackLog,
  fetchReadiness,
  indexHref,
  itemHref,
  makePack,
  registerItems,
  type InspectionPack,
  type InspectionPackDetail,
  type ItemState,
  type PackItem,
  type ReadinessPack,
} from "./packs-data";

const STATE_VARIANTS: Record<ItemState, PillVariant> = {
  ready: "positive",
  expiring: "progress",
  no_copy: "review",
  incomplete: "review",
  expired: "negative",
  missing: "negative",
  left_out: "tag",
  not_tracked: "tag",
};

export function ItemStatePill({ state }: { state: ItemState }) {
  const t = useTranslations("compliance.packs.state");
  return (
    <Pill
      variant={STATE_VARIANTS[state]}
      icon={state === "expired" || state === "missing" ? "alert" : null}
    >
      {t(state)}
    </Pill>
  );
}

/** One line about an item: dates and counts only (the same facts the printed index shows). */
export function ItemDetails({ item }: { item: PackItem }) {
  const t = useTranslations("compliance.packs.details");
  let text: string;
  switch (item.source) {
    case "certificate":
      if (item.state === "missing") {
        text = t("certificateMissing");
      } else {
        const until = item.valid_until
          ? t("validUntil", { date: formatDate(item.valid_until) ?? item.valid_until })
          : t("noExpiry");
        text = `${until} · ${item.has_copy ? t("copy") : t("noCopy")}`;
      }
      break;
    case "register":
      text =
        item.included === false
          ? t("registerLeftOut")
          : item.included
            ? t("registerChosen")
            : t("registerReady");
      break;
    case "disclosure":
      text = item.published_at
        ? t("published", { date: formatDate(item.published_at) ?? "" })
        : t("notPublished");
      break;
    case "privacy_notice":
      text = item.issued_on
        ? t("noticeGiven", { date: formatDate(item.issued_on) ?? item.issued_on })
        : t("noNotice");
      break;
    case "dpdp_readiness":
      text = t("dpdp", { done: item.done ?? 0, applicable: item.applicable ?? 0 });
      break;
    default:
      text = t("notTracked", { feature: item.feature ?? "" });
  }
  return <span className="text-sm break-words text-ink-muted">{text}</span>;
}

function Counts({ pack }: { pack: Pick<ReadinessPack, "counts"> }) {
  const t = useTranslations("compliance.packs.counts");
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatCard label={t("ready")} value={String(pack.counts.ready)} unavailableLabel="" />
      <StatCard label={t("attention")} value={String(pack.counts.attention)} unavailableLabel="" />
      <StatCard label={t("missing")} value={String(pack.counts.missing)} unavailableLabel="" />
      <StatCard
        label={t("notTracked")}
        value={String(pack.counts.not_tracked)}
        unavailableLabel=""
      />
    </dl>
  );
}

function ItemsTable({ pack }: { pack: ReadinessPack }) {
  const t = useTranslations("compliance.packs");
  const columns: ColumnV2<PackItem>[] = [
    {
      key: "item",
      stack: "title",
      header: t("colItem"),
      cell: (item) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold break-words text-ink">{item.label}</span>
          {item.note ? <span className="text-xs text-ink-muted">{item.note}</span> : null}
        </span>
      ),
    },
    { key: "state", header: t("colState"), cell: (item) => <ItemStatePill state={item.state} /> },
    { key: "details", header: t("colDetails"), cell: (item) => <ItemDetails item={item} /> },
    {
      key: "fix",
      header: t("colFixBy"),
      cell: (item) => <span className="text-sm">{item.fix_by}</span>,
    },
    {
      key: "where",
      header: t("colWhere"),
      cell: (item) => {
        const href = itemHref(item);
        if (!href) return <span className="text-sm text-ink-muted">{t("ownFiles")}</span>;
        return (
          <Link
            href={href}
            className="text-sm text-primary underline-offset-4 hover:underline"
            aria-label={t("openAria", { name: item.label })}
          >
            {t(`where.${item.source}`)}
          </Link>
        );
      },
    },
  ];
  return (
    <DataTableV2
      id={`inspection-pack-${pack.key}`}
      stacked
      caption={pack.label}
      captionHidden
      columns={columns}
      state={{ status: "ready", data: pack.items }}
      rowKey={(item) => item.key}
      emptyTitle={t("emptyTitle")}
      emptyBody={t("emptyBody")}
    />
  );
}

/** "Make the pack" (FR-COMP-032..FR-COMP-037): year, visit date, the registers to include. */
function MakePackCard({ pack }: { pack: ReadinessPack }) {
  const t = useTranslations("compliance.packs.make");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const mayRegisters = can(REGISTER_READ);
  const formId = useId();
  const [yearId, setYearId] = useState("");
  const [visitOn, setVisitOn] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [made, setMade] = useState<InspectionPackDetail | null>(null);
  const [key, setKey] = useState(() => newIdempotencyKey());
  const years = useQuery({
    queryKey: ["staff", "compliance", "pack-years"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/academic-years", {
          params: { query: { limit: 200, include_archived: true } },
        }),
      ),
    retry: false,
  });
  const registers = registerItems(pack);
  const mutation = useApiMutation(
    () =>
      makePack(
        api,
        {
          pack_key: pack.key,
          academic_year_id: yearId || null,
          visit_on: visitOn || null,
          registers: mayRegisters ? chosen : [],
        },
        key,
      ),
    {
      invalidate: [PACKS_KEYS.log],
      onSuccess: (result) => {
        setMade(result);
        setKey(newIdempotencyKey());
      },
    },
  );
  const yearOptions = (years.data?.data ?? []).map((year) => ({
    value: year.id,
    label: year.is_current ? t("currentYear", { label: year.label }) : year.label,
  }));

  return (
    <Card title={t("title")} description={t("body")}>
      <form
        id={formId}
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          setMade(null);
          mutation.mutate(undefined);
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <SelectField
            label={t("year")}
            value={yearId}
            placeholder={t("yearDefault")}
            onChange={(event) => setYearId(event.target.value)}
            options={yearOptions}
          />
          <TextField
            label={t("visitOn")}
            type="date"
            value={visitOn}
            onChange={(event) => setVisitOn(event.target.value)}
            hint={t("visitOnHint")}
          />
        </div>
        {registers.length > 0 ? (
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-ink">{t("registers")}</legend>
            {mayRegisters ? (
              <>
                <p className="text-sm text-ink-muted">{t("registersHint")}</p>
                {registers.map((item) => (
                  <label key={item.key} className="flex min-h-8 cursor-pointer items-start gap-3">
                    <input
                      type="checkbox"
                      checked={chosen.includes(item.key)}
                      onChange={(event) =>
                        setChosen((now) =>
                          event.target.checked
                            ? [...now, item.key]
                            : now.filter((one) => one !== item.key),
                        )
                      }
                      className="mt-1 size-4 shrink-0 accent-primary"
                    />
                    <span className="text-sm text-ink">{item.label}</span>
                  </label>
                ))}
              </>
            ) : (
              <p className="text-sm text-ink-muted">{t("registersNotAllowed")}</p>
            )}
          </fieldset>
        ) : null}
        {mutation.isError ? <ApiErrorAlert error={mutation.error} namespace="compliance" /> : null}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={mutation.isPending}>
            {mutation.isPending ? tc("working") : t("submit")}
          </Button>
          {made ? (
            <a
              href={indexHref(made.id)}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonClasses("secondary", "md")}
            >
              <Icon name="file" className="size-4" />
              {t("printIndex")}
              <span className="sr-only"> ({t("newTab")})</span>
            </a>
          ) : null}
        </div>
        {made ? (
          <p role="status" className="text-sm text-ink-muted">
            {t("made", { ready: made.counts.ready, missing: made.counts.missing })}
          </p>
        ) : null}
      </form>
    </Card>
  );
}

function PackLog() {
  const t = useTranslations("compliance.packs.log");
  const api = useBffClient("staff");
  const log = useQuery({
    queryKey: PACKS_KEYS.log,
    queryFn: () => fetchPackLog(api),
    retry: false,
  });
  const columns: ColumnV2<InspectionPack>[] = [
    {
      key: "made",
      stack: "title",
      header: t("colMade"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold break-words text-ink">{row.pack_label}</span>
          <span className="num text-xs text-ink-muted">
            {formatDateTime(row.generated_at)}
            {row.generated_by_name ? ` · ${row.generated_by_name}` : ""}
          </span>
        </span>
      ),
    },
    {
      key: "visit",
      header: t("colVisit"),
      cell: (row) => (
        <span className="num text-sm">{row.visit_on ? formatDate(row.visit_on) : "—"}</span>
      ),
    },
    {
      key: "summary",
      header: t("colSummary"),
      cell: (row) => (
        <span className="text-sm text-ink-muted">
          {t("summary", {
            ready: row.counts.ready,
            attention: row.counts.attention,
            missing: row.counts.missing,
          })}
        </span>
      ),
    },
    {
      key: "index",
      header: t("colIndex"),
      cell: (row) => (
        <a
          href={indexHref(row.id)}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-primary underline-offset-4 hover:underline"
          aria-label={t("printAria", { name: row.pack_label })}
        >
          {t("print")}
          <span className="sr-only"> ({t("newTab")})</span>
        </a>
      ),
    },
  ];
  return (
    <section className="space-y-3" aria-labelledby="inspection-pack-log">
      <h2 id="inspection-pack-log" className="text-lg font-semibold text-ink">
        {t("title")}
      </h2>
      <DataTableV2
        id="inspection-pack-log-table"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable(log)}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
        onRetry={() => void log.refetch()}
      />
    </section>
  );
}

/**
 * "Prepare for an inspection" (FC-07, FO-12): pick the authority, see each item's state with
 * where to fix or print it, make the pack (the index prints from the API) and see the packs made.
 */
export function InspectionScreen() {
  const t = useTranslations("compliance.packs");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const tCompliance = useTranslations("compliance");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(COMPLIANCE_READ);
  const manage = can(COMPLIANCE_MANAGE);
  const [packKey, setPackKey] = useState("");
  const readiness = useQuery({
    queryKey: PACKS_KEYS.readiness,
    queryFn: () => fetchReadiness(api),
    enabled: allowed,
    retry: false,
  });
  const packs = useMemo(() => readiness.data?.packs ?? [], [readiness.data]);
  const pack = packs.find((one) => one.key === packKey) ?? packs[0] ?? null;

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[
        { label: tn("home"), href: "/" },
        { label: tCompliance("title"), href: "/compliance" },
        { label: t("title") },
      ]}
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
        <Alert tone="info" title={tCompliance("noPermissionTitle")}>
          {tCompliance("noPermissionBody")}
        </Alert>
      </div>
    );
  }
  return (
    <div className="space-y-5">
      {header}
      {readiness.isPending ? <LoadingState label={tc("loading")} /> : null}
      {readiness.isError ? <ApiErrorAlert error={readiness.error} namespace="compliance" /> : null}
      {readiness.data && !readiness.data.verified ? (
        <Alert tone="info" title={t("unverifiedTitle")}>
          {t("unverifiedBody")}
        </Alert>
      ) : null}
      {pack ? (
        <>
          <div className="max-w-md">
            <SelectField
              label={t("choosePack")}
              value={pack.key}
              onChange={(event) => setPackKey(event.target.value)}
              options={packs.map((one) => ({ value: one.key, label: one.label }))}
            />
          </div>
          <Card title={pack.label} description={t("authority", { authority: pack.authority })}>
            <div className="space-y-4">
              <p className="text-sm text-ink-muted">{pack.note}</p>
              <Counts pack={pack} />
            </div>
          </Card>
          <ItemsTable pack={pack} />
          {manage ? <MakePackCard key={pack.key} pack={pack} /> : null}
        </>
      ) : null}
      <PackLog />
    </div>
  );
}
