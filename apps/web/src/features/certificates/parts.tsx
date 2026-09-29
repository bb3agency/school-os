"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import {
  CERT_KEYS,
  certificatePill,
  type Certificate,
  type CertificateStatus,
  type CertificateTypeInfo,
} from "./types";

/** Workflow pill with the status in words (never colour alone). */
export function CertificateStatusBadge({
  status,
  size = "sm",
}: {
  status: CertificateStatus;
  size?: "sm" | "md";
}) {
  const t = useTranslations("certificates.status");
  return (
    <Pill variant={certificatePill[status]} size={size}>
      {t(status)}
    </Pill>
  );
}

/** GET /certificates/types (labels, inputs and choices in both languages; config.yaml). */
export function useCertificateTypes() {
  const api = useBffClient("staff");
  return useApiQuery(CERT_KEYS.types, async () => unwrap(api.GET("/api/v1/certificates/types")));
}

/** A label in the reader's language (the API sends both). */
export function localLabel(item: { label_en: string; label_te: string }, locale: string): string {
  return locale === "te" && item.label_te ? item.label_te : item.label_en;
}

/** "Choice value → label" for the inputs a certificate was requested with. */
export function inputText(
  types: readonly CertificateTypeInfo[] | null,
  certificate: Pick<Certificate, "certificate_type">,
  key: string,
  value: string,
  locale: string,
): string {
  const input = types
    ?.find((type) => type.key === certificate.certificate_type)
    ?.inputs.find((one) => one.key === key);
  const choice = input?.choices.find((one) => one.value === value);
  if (choice) return localLabel(choice, locale);
  if (input?.kind === "date" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split("-");
    return `${d}/${m}/${y}`;
  }
  return value;
}

/** The certificate's name: its type in the reader's language and, once issued, the serial. */
export function useCertificateTitle() {
  const t = useTranslations("certificates");
  return (certificate: Pick<Certificate, "certificate_type" | "serial" | "duplicate_no">) => {
    const type = t(`types.${certificate.certificate_type}`);
    if (!certificate.serial) return type;
    return certificate.duplicate_no
      ? t("titleDuplicate", { type, serial: certificate.serial, copy: certificate.duplicate_no })
      : t("titleSerial", { type, serial: certificate.serial });
  };
}

/** The print view is an API page with its own strict CSP, served through the BFF. */
export function PrintViewLink({ certificateId }: { certificateId: string }) {
  const t = useTranslations("certificates.detail");
  return (
    <a
      href={`/bff/api/v1/certificates/${certificateId}/print`}
      target="_blank"
      rel="noopener noreferrer"
      className={buttonClasses("secondary", "md")}
    >
      <Icon name="file" className="size-4" />
      {t("openPrint")}
      <span className="sr-only"> ({t("newTab")})</span>
    </a>
  );
}

/**
 * PDF download (FR-CERT-011): a presigned link valid for at most 5 minutes is fetched on
 * demand and opened at once; it is never stored. Until the PDF is stored and checked, the
 * button explains why it is not ready.
 */
export function DownloadPdfButton({ certificate }: { certificate: Certificate }) {
  const t = useTranslations("certificates.detail");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const ready = certificate.pdf_status === "ready";
  async function download() {
    setPending(true);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/certificates/{certificate_id}/download-url", {
          params: { path: { certificate_id: certificate.id } },
        }),
      );
      window.location.assign(link.url);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="space-y-2">
      <Button onClick={() => void download()} disabled={!ready || pending}>
        <Icon name="arrowDown" className="size-4" />
        {pending ? tc("working") : t("downloadPdf")}
      </Button>
      {!ready ? (
        <p className="text-sm text-ink-muted">
          {certificate.pdf_status === "failed" ? t("pdfFailed") : t("pdfPreparing")}
        </p>
      ) : null}
      <ApiErrorAlert error={error} namespace="certificates" />
    </div>
  );
}
