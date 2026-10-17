"use client";

import type { TenantProfile } from "@schoolos/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { ifMatch } from "./data";

/** The languages a school may write to parents in (ADR-0044). */
export const PARENT_LANGUAGES = ["en", "te"] as const;
export type ParentLanguage = (typeof PARENT_LANGUAGES)[number];

/**
 * The school's parent language (ADR-0044, FR-LANG-001, US-2301): the language of WhatsApp
 * messages, parent link pages and printed parent forms. Independent of the staff UI language,
 * which stays English while Telugu is hidden (ADR-0036): the card names the languages in
 * English. Changing it needs `tenant.settings.manage` and a recent MFA sign-in (428 is handled
 * globally); the API audits the change. While Telugu is chosen, the card says when some parent
 * texts have not been checked by a fluent reviewer yet (FR-LANG-005, D23).
 */
export function ParentLanguageCard({
  tenant,
  manage,
  tenantKey,
}: {
  tenant: TenantProfile;
  manage: boolean;
  tenantKey: readonly unknown[];
}) {
  const t = useTranslations("schoolSettings.parentLanguage");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const current: ParentLanguage = tenant.settings.parent_language === "te" ? "te" : "en";
  const unreviewed = tenant.parent_language_unreviewed ?? [];

  async function save(language: ParentLanguage) {
    setPending(true);
    setSaved(false);
    setError(undefined);
    try {
      await unwrap(
        api.PATCH("/api/v1/tenant", {
          headers: { "If-Match": ifMatch(tenant.version) },
          body: { parent_language: language },
        }),
      );
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: tenantKey });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-3">
        <p className="text-sm">
          {t("current")} <strong>{t(`languages.${current}`)}</strong>
        </p>
        {current === "te" && unreviewed.length > 0 ? (
          <Alert tone="warning">{t("unreviewed")}</Alert>
        ) : null}
        {manage ? (
          <div className="flex flex-wrap gap-2">
            {PARENT_LANGUAGES.filter((language) => language !== current).map((language) => (
              <Button
                key={language}
                variant="secondary"
                size="sm"
                disabled={pending}
                onClick={() => void save(language)}
              >
                <Icon name="check" className="size-4" />
                {pending ? tc("working") : t("switchTo", { language: t(`languages.${language}`) })}
              </Button>
            ))}
          </div>
        ) : null}
        <div role="status" aria-live="polite">
          {saved ? <p className="text-sm">{t("saved")}</p> : null}
        </div>
        <ApiErrorAlert error={error} />
      </div>
    </Card>
  );
}
