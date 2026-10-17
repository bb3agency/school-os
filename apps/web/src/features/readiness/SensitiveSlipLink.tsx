"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { slipHref, type SlipTarget } from "./types";

/** Who may print the Aadhaar-as-printed values on slips (the API checks it again, with scope). */
export const SENSITIVE_PERMISSION = "student.read_sensitive";

/**
 * Slips WITH the Aadhaar-as-printed values (SEC-045, docs/07 §11). The normal print link masks
 * them. "Show Aadhaar details" first checks through the BFF that the slips can be printed with
 * them (`check=true`: nothing is printed or audited); the API asks for a recent MFA sign-in,
 * which the BFF client handles ("confirm it's you", then the same request again). Then "Open
 * slips with Aadhaar details" opens the print view in a new tab (a real link, so no pop-up
 * blocker gets in the way); that view is audited with the students it showed. Shown only to
 * holders of student.read_sensitive.
 */
export function SensitiveSlipLink({
  profileKey,
  target,
}: {
  profileKey: string;
  target: SlipTarget;
}) {
  const t = useTranslations("readiness");
  const tc = useTranslations("common");
  const can = useStaffCan();
  const api = useBffClient("staff");
  const [pending, setPending] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  if (!can(SENSITIVE_PERMISSION)) return null;

  async function prepare() {
    setPending(true);
    setError(undefined);
    setReady(false);
    try {
      await unwrap(
        api.GET("/api/v1/dq/readiness/{profile_key}/slips", {
          params: {
            path: { profile_key: profileKey },
            query: {
              ...("studentId" in target
                ? { student_id: target.studentId }
                : { section_id: target.sectionId }),
              include_sensitive: true,
              check: true,
            },
          },
          parseAs: "text",
        }),
      );
      setReady(true);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <span className="flex flex-col gap-2">
      <span className="flex flex-wrap items-center gap-2">
        {ready ? (
          <a
            href={slipHref(profileKey, target, { includeSensitive: true })}
            target="_blank"
            rel="noopener noreferrer"
            className={buttonClasses("secondary", "md")}
          >
            <Icon name="file" className="size-4" />
            {t("openSensitiveSlips")}
            <span className="sr-only"> ({t("newTab")})</span>
          </a>
        ) : (
          <Button variant="secondary" onClick={() => void prepare()} disabled={pending}>
            {pending ? tc("working") : t("showSensitiveSlips")}
          </Button>
        )}
      </span>
      {ready ? <span className="text-sm text-ink-muted">{t("sensitiveSlipsNote")}</span> : null}
      <ApiErrorAlert error={error} namespace="exports" />
    </span>
  );
}
