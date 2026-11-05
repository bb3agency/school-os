"use client";

import type { SchoolChoices } from "@schoolos/api-client";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { BREACH_KEYS, BREACH_READ, type DraftKind } from "./types";

/** The print page of one breach draft (D60): `/privacy/breaches/{id}/drafts/{draft}/print`. */
export function draftPrintHref(breachId: string, draft: DraftKind): string {
  return `/privacy/breaches/${encodeURIComponent(breachId)}/drafts/${draft}/print`;
}

/**
 * A breach notice draft as an A4 page (D60; ADR-0050, FR-DPDP-036): the school's name as the
 * header, the draft's title and text, its template version. The console's menus, the
 * breadcrumb and the buttons carry `data-print="hide"`, so printing gives only the sheet.
 * Needs `privacy.breach.read`; the text comes from `GET /privacy/breaches/{id}/drafts/{draft}`
 * (nothing is sent from here).
 */
export function DraftPrintScreen({ breachId, draft }: { breachId: string; draft: DraftKind }) {
  const t = useTranslations("breaches.print");
  const tb = useTranslations("breaches");
  const td = useTranslations("breaches.detail");
  const tdr = useTranslations("breaches.drafts");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const me = useStaffMeQuery();
  const readable = can(BREACH_READ);
  const text = useQuery({
    queryKey: BREACH_KEYS.draft(breachId, draft),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/privacy/breaches/{breach_id}/drafts/{draft}", {
          params: { path: { breach_id: breachId, draft } },
        }),
      ),
    enabled: readable,
    retry: false,
  });
  // The school's name for the header (GET /me/schools: any staff member, no personal data).
  const schools = useQuery({
    queryKey: ["staff", "me", "schools"],
    queryFn: () => unwrap(api.GET("/api/v1/me/schools")) as Promise<SchoolChoices>,
    enabled: readable,
    staleTime: 300_000,
    retry: false,
  });
  const schoolName =
    schools.data?.data.find((school) => school.tenant_id === me.data?.tenant_id)?.name ?? null;

  const header = (
    <div data-print="hide">
      <PageHeader
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: tb("title"), href: "/privacy/breaches" },
          { label: td("crumb"), href: `/privacy/breaches/${breachId}` },
          { label: t("crumb") },
        ]}
        title={t("title", { draft: tdr(`${draft}.name`) })}
        description={t("description")}
        actions={
          text.data ? (
            <Button onClick={() => window.print()}>
              <Icon name="printer" className="size-4" />
              {t("print")}
            </Button>
          ) : undefined
        }
      />
    </div>
  );

  if (!me.isPending && !readable) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={tb("noPermissionTitle")}>
          {tb("noPermissionBody")}
        </Alert>
      </div>
    );
  }
  if (me.isPending || text.isPending) {
    return (
      <div className="space-y-5">
        {header}
        <LoadingState label={tc("loading")} />
      </div>
    );
  }
  if (!text.data) {
    return (
      <div className="space-y-5">
        {header}
        <ApiErrorAlert error={text.error} namespace="breaches" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {header}
      <article
        aria-labelledby="breach-draft-title"
        className="mx-auto max-w-[210mm] rounded-lg border border-border bg-surface p-6 text-ink sm:p-10 print:max-w-none print:rounded-none print:border-0 print:p-0"
      >
        <header className="mb-6 border-b border-border pb-3 text-center print:border-black">
          <p className="text-xl font-semibold break-words">{schoolName ?? t("schoolFallback")}</p>
        </header>
        <h2 id="breach-draft-title" className="mb-4 text-lg font-semibold">
          {text.data.title}
        </h2>
        <div className="text-[11pt] leading-relaxed break-words whitespace-pre-wrap">
          {text.data.text}
        </div>
        <footer className="mt-8 text-xs text-ink-muted print:text-black">
          {tdr("templateVersion", { version: text.data.template_version })}
        </footer>
      </article>
      <p className="text-sm text-ink-muted" data-print="hide">
        {tdr("notSent")}
      </p>
    </div>
  );
}
