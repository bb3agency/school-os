"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
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
    <Card title={t(`${kind}.title`)} description={t(`${kind}.body`)}>
      <div className="space-y-4">
        <div className="grid gap-4">
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
          <Button variant="secondary" onClick={() => void prepare()} disabled={pending}>
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
    <div className="space-y-6">
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
        <div className="grid gap-4 xl:grid-cols-3">
          {REGISTERS.map((kind) => (
            <RegisterCard key={kind} kind={kind} years={years.data.data} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
