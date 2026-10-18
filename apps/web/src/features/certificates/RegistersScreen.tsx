"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import {
  REGISTER_PATHS,
  REGISTER_READ,
  REGISTERS,
  type CertificateType,
  type RegisterKind,
} from "./types";

const OTHER_TYPES = ["bonafide", "study", "conduct"] as const satisfies readonly CertificateType[];

type Api = ReturnType<typeof useBffClient>;

/** The query string of a register print view (IDs and codes only; docs/09 §2). */
export function registerQuery(kind: RegisterKind, yearId: string, type: string): string {
  const params = new URLSearchParams();
  if (yearId) params.set("academic_year_id", yearId);
  if (kind === "certificates" && type) params.set("certificate_type", type);
  const text = params.toString();
  return text ? `?${text}` : "";
}

/**
 * Check through the BFF that a register can be printed now (openapi-fetch needs each path
 * spelled out): `check=true` answers 204 without the page and is not audited, so only opening
 * the print view counts as a view (`register.viewed`, once). A 428 step-up answer is handled by
 * the BFF client (confirm it's you, then the same request again).
 */
async function checkRegister(api: Api, kind: RegisterKind, yearId: string, type: string) {
  const query = { check: true, ...(yearId ? { academic_year_id: yearId } : {}) };
  if (kind === "transfer") {
    await unwrap(
      api.GET("/api/v1/registers/transfer-certificates", {
        params: { query },
        parseAs: "text",
      }),
    );
    return;
  }
  if (kind === "certificates") {
    const certificateType = OTHER_TYPES.find((item) => item === type);
    await unwrap(
      api.GET("/api/v1/registers/certificates", {
        params: {
          query: { ...query, ...(certificateType ? { certificate_type: certificateType } : {}) },
        },
        parseAs: "text",
      }),
    );
    return;
  }
  await unwrap(
    api.GET("/api/v1/registers/admission-withdrawal", {
      params: { query },
      parseAs: "text",
    }),
  );
}

/**
 * One register (FR-REG-001..004). Registers carry many students' details, so the API asks for
 * a recent MFA sign-in (step-up). "Prepare" checks that first (confirming it's you in a
 * separate window when needed); "Open print view" then opens the API's A4 page, with its own
 * strict CSP, in a new tab (a real link, so no pop-up blocker gets in the way).
 */
function RegisterCard({
  kind,
  years,
}: {
  kind: RegisterKind;
  years: readonly { id: string; label: string; is_current: boolean }[];
}) {
  const t = useTranslations("certificates.registers");
  const tt = useTranslations("certificates.types");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const current = years.find((year) => year.is_current)?.id ?? "";
  const [yearId, setYearId] = useState(current);
  const [type, setType] = useState("");
  const [ready, setReady] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const year = yearId || current;
  const yearLabel = years.find((one) => one.id === year)?.label ?? null;
  const href = `/bff${REGISTER_PATHS[kind]}${registerQuery(kind, year, type)}`;

  async function prepare() {
    setPending(true);
    setError(undefined);
    setReady(null);
    try {
      await checkRegister(api, kind, year, kind === "certificates" ? type : "");
      setReady(href);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card
      title={t(`${kind}.title`)}
      description={t(`${kind}.body`)}
      actions={
        <Pill variant="tag" icon="file">
          {t("a4")}
        </Pill>
      }
    >
      <div className="space-y-4">
        {/* The sheet as it prints: an A4 landscape page with the register's heading. The rows
            themselves are only in the print view (opening it is the one audited view). */}
        <div
          aria-hidden="true"
          className="flex flex-col justify-between gap-4 rounded-md border border-border bg-surface p-4 sm:aspect-[297/210] sm:p-6"
          data-print="hide"
        >
          <div className="space-y-1 border-b border-border pb-3 text-center">
            <p className="text-xs font-semibold tracking-wide text-ink-muted uppercase">
              {t("sheetSchool")}
            </p>
            <p className="font-semibold text-ink">{t(`${kind}.title`)}</p>
            {yearLabel ? (
              <p className="num text-sm text-ink-muted">{t("sheetYear", { label: yearLabel })}</p>
            ) : null}
          </div>
          <div className="grid flex-1 grid-cols-6 gap-px overflow-hidden rounded-sm border border-border bg-border">
            {Array.from({ length: 24 }, (_, index) => (
              <span
                key={index}
                className={index < 6 ? "min-h-5 bg-surface-muted" : "min-h-5 bg-surface"}
              />
            ))}
          </div>
          <p className="text-center text-xs text-ink-muted">{t("sheetNote")}</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <SelectField
            label={t("year")}
            value={year}
            onChange={(event) => {
              setYearId(event.target.value);
              setReady(null);
            }}
            options={years.map((one) => ({
              value: one.id,
              label: one.is_current ? t("currentYear", { label: one.label }) : one.label,
            }))}
          />
          {kind === "certificates" ? (
            <SelectField
              label={t("type")}
              value={type}
              placeholder={t("allTypes")}
              onChange={(event) => {
                setType(event.target.value);
                setReady(null);
              }}
              options={OTHER_TYPES.map((item) => ({ value: item, label: tt(item) }))}
            />
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant={ready ? "secondary" : "primary"}
            onClick={() => void prepare()}
            disabled={pending}
          >
            {pending ? tc("working") : t("prepare")}
          </Button>
          {ready ? (
            <a
              href={ready}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonClasses("primary", "md")}
            >
              <Icon name="file" className="size-4" />
              {t("open")}
              <span className="sr-only"> ({t("newTab")})</span>
            </a>
          ) : null}
        </div>
        {ready ? <p className="text-sm text-ink-muted">{t("readyNote")}</p> : null}
        <ApiErrorAlert error={error} namespace="certificates" />
      </div>
    </Card>
  );
}

/**
 * Register print views (US-1106; FR-REG-001..004, BR-11): the TC register (counterfoil), the
 * certificate issue register and the admission and withdrawal register, per academic year, in
 * A4 landscape with bilingual headings, for the school's paper registers.
 */
export function RegistersScreen() {
  const t = useTranslations("certificates.registers");
  const tcert = useTranslations("certificates");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const [kind, setKind] = useState<RegisterKind>("transfer");
  const years = useQuery({
    queryKey: ["staff", "certificates", "register-years"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/academic-years", {
          params: { query: { limit: 200, include_archived: true } },
        }),
      ),
    retry: false,
  });
  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: tcert("crumbHome"), href: "/" },
          { label: tcert("title"), href: "/certificates" },
          { label: t("title") },
        ]}
      />
      {!can(REGISTER_READ) ? <Alert tone="info">{t("noPermission")}</Alert> : null}
      <Alert tone="info" title={t("stepUpTitle")}>
        {t("stepUpBody")}
      </Alert>
      {years.isError ? <ApiErrorAlert error={years.error} namespace="certificates" /> : null}
      {years.isPending ? <LoadingState label={tc("loading")} /> : null}
      {years.data ? (
        // Print-first: the register picker on the left (on top on phones), the chosen
        // register's A4 sheet beside it.
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(15rem,20rem)_minmax(0,1fr)]">
          <Card padding="sm">
            <fieldset className="space-y-2">
              <legend className="mb-2 text-sm font-semibold text-ink">{t("pickerLegend")}</legend>
              {REGISTERS.map((item) => (
                <label
                  key={item}
                  className="grid cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-0.5 rounded-lg border border-border-soft p-3 text-sm has-[:checked]:border-primary has-[:checked]:bg-primary-soft"
                >
                  {/* Named by the register's title; its one-line summary is the description. */}
                  <input
                    type="radio"
                    name="register"
                    value={item}
                    checked={kind === item}
                    onChange={() => setKind(item)}
                    aria-labelledby={`register-${item}-title`}
                    aria-describedby={`register-${item}-body`}
                    className="row-span-2 mt-0.5 size-4"
                  />
                  <span id={`register-${item}-title`} className="min-w-0 font-semibold text-ink">
                    {t(`${item}.title`)}
                  </span>
                  <span id={`register-${item}-body`} className="col-start-2 min-w-0 text-ink-muted">
                    {t(`${item}.body`)}
                  </span>
                </label>
              ))}
            </fieldset>
          </Card>
          <RegisterCard key={kind} kind={kind} years={years.data.data} />
        </div>
      ) : null}
    </div>
  );
}
