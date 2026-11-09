"use client";

import type { components } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { formatDate } from "@/lib/format";
import { StatePill } from "./ComplianceScreen";

/**
 * FC-01 (FR-COMP-020, FR-COMP-021) on the CBSE Appendix IX draft: the compliance register's
 * current certificate for a document item and its state, with a button to use its copy.
 */
export type RegisterCertificate = components["schemas"]["RegisterCertificateOut"];
type DisclosureDraft = components["schemas"]["DisclosureDraftOut"];

/** The register's certificates by item key from a draft answer (empty when it has none). */
export function registerCertificates(
  draft: Pick<DisclosureDraft, "certificates">,
): Record<string, RegisterCertificate[]> {
  return draft.certificates ?? {};
}

export function hasExpired(certificates: Record<string, RegisterCertificate[]>): boolean {
  return Object.values(certificates).some((list) => list.some((c) => c.state === "expired"));
}

export function RegisterNote({
  certificates,
  chosen,
  onUse,
}: {
  certificates: RegisterCertificate[] | undefined;
  chosen: string[];
  onUse: (documentId: string) => void;
}) {
  const t = useTranslations("disclosure.certificates");
  if (!certificates?.length) return null;
  return (
    <ul className="space-y-2">
      {certificates.map((c) => (
        <li key={c.certificate_id} className="flex flex-wrap items-center gap-2 text-sm">
          <span className="min-w-0 break-words">
            {t("fromRegister", { name: c.kind_label })}
            {", "}
            {c.valid_until
              ? t(c.state === "expired" ? "registerExpired" : "registerValid", {
                  date: formatDate(c.valid_until) ?? c.valid_until,
                })
              : t("registerNoExpiry")}
          </span>
          <StatePill state={c.state} />
          {c.document_id && c.publishable && !chosen.includes(c.document_id) ? (
            <Button variant="secondary" size="sm" onClick={() => onUse(c.document_id ?? "")}>
              {t("useRegisterCopy")}
            </Button>
          ) : null}
          {c.document_id && !c.publishable ? (
            <span className="text-xs text-ink-muted">{t("registerNotPublishable")}</span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function ExpiredWarning() {
  const t = useTranslations("disclosure.certificates");
  return (
    <Alert tone="warning" title={t("expiredWarningTitle")}>
      {t("expiredWarningBody")}
    </Alert>
  );
}
