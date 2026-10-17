"use client";

import type { TenantProfile } from "@schoolos/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { CERT_KEYS, ifMatch } from "./types";

type FormatKey = "generic-v1" | "ap-tc-2027";
type Patch = { certificate_format?: { transfer: FormatKey }; print_restricted_tc_fields?: boolean };

/**
 * The school's transfer certificate format and whether religion and caste are printed on it
 * (ADR-0045 decisions 1 and 3; FR-CERT-015, FR-CERT-018; owner decisions D3, D15). Holders of
 * `tenant.settings.manage` change them (a recent MFA sign-in; 428 is handled globally); the API
 * audits both, the restricted switch with its field names. Issued certificates never change.
 */
export function CertificateFormatCard({
  tenant,
  manage,
  tenantKey,
}: {
  tenant: TenantProfile;
  manage: boolean;
  tenantKey: readonly unknown[];
}) {
  const t = useTranslations("certificates.format");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const formats = useApiQuery(CERT_KEYS.formats, () =>
    unwrap(api.GET("/api/v1/certificates/formats")),
  );
  const current: FormatKey = tenant.settings.certificate_format?.transfer ?? "generic-v1";
  const restricted = tenant.settings.print_restricted_tc_fields === true;
  const list = formats.status === "ready" ? formats.data : [];
  const currentFormat = list.find((format) => format.key === current);

  async function save(body: Patch) {
    setPending(true);
    setSaved(false);
    setError(undefined);
    try {
      await unwrap(
        api.PATCH("/api/v1/tenant", { headers: { "If-Match": ifMatch(tenant.version) }, body }),
      );
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: tenantKey });
      await queryClient.invalidateQueries({ queryKey: CERT_KEYS.types });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-4">
        <p className="text-sm">
          {t("current")} <strong>{t(`names.${current}`)}</strong>
        </p>
        {currentFormat && !currentFormat.verified ? (
          <Alert tone="warning">{t("unverified")}</Alert>
        ) : null}
        {manage ? (
          <div className="flex flex-wrap gap-2">
            {list
              .filter((format) => format.key !== current)
              .map((format) => (
                <Button
                  key={format.key}
                  variant="secondary"
                  size="sm"
                  disabled={pending}
                  onClick={() =>
                    void save({ certificate_format: { transfer: format.key as FormatKey } })
                  }
                >
                  {pending
                    ? tc("working")
                    : t("switchTo", { format: t(`names.${format.key as FormatKey}`) })}
                </Button>
              ))}
          </div>
        ) : null}
        <div className="space-y-2 border-t border-border pt-4">
          <h3 className="text-sm font-semibold">{t("restrictedTitle")}</h3>
          <p className="text-sm">{restricted ? t("restrictedOn") : t("restrictedOff")}</p>
          <p className="text-sm text-ink-muted">{t("restrictedHint")}</p>
          {manage ? (
            <Button
              variant={restricted ? "secondary" : "danger"}
              size="sm"
              disabled={pending}
              onClick={() => void save({ print_restricted_tc_fields: !restricted })}
            >
              {pending ? tc("working") : restricted ? t("leaveBlank") : t("printThem")}
            </Button>
          ) : null}
        </div>
        <div role="status" aria-live="polite">
          {saved ? <p className="text-sm">{t("saved")}</p> : null}
        </div>
        <ApiErrorAlert error={error} />
      </div>
    </Card>
  );
}
