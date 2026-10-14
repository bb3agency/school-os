"use client";

import type { components } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Link } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { penDigits } from "./StudentList";

type CheckResult = components["schemas"]["NationalIdCheckOut"];

/**
 * Transfer-in by PEN (FR-STU-018, ADR-0039): before admitting a child, ask whether this school
 * already has a record with the PEN, and what to do in UDISE+ (open or re-admit the existing
 * record, import the child by PEN, or search UDISE+ first). The PEN goes in the request body,
 * never the URL (SEC-008); nothing is written.
 */
export function PenCheck({ pen }: { pen: string }) {
  const t = useTranslations("students.penCheck");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [result, setResult] = useState<CheckResult | null>(null);
  const [checked, setChecked] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [invalid, setInvalid] = useState(false);
  const digits = penDigits(pen);

  async function check() {
    setError(undefined);
    setResult(null);
    if (!digits) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setPending(true);
    try {
      setResult(
        await unwrap(
          api.POST("/api/v1/students/national-id-check", { body: { udise_pen: digits } }),
        ),
      );
      setChecked(digits);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const stale = result !== null && checked !== digits;
  return (
    <div className="space-y-3">
      <Button variant="secondary" size="sm" onClick={() => void check()} disabled={pending}>
        {pending ? tc("working") : t("check")}
      </Button>
      <div role="status" aria-live="polite">
        {invalid ? <p className="text-sm text-danger">{t("invalid")}</p> : null}
        {result && !stale ? (
          <Alert
            tone={result.udise_action === "import_by_pen" ? "info" : "warning"}
            title={t(`actions.${result.udise_action}.title`)}
          >
            <p>{t(`actions.${result.udise_action}.body`)}</p>
            {result.matches.length > 0 ? (
              <ul className="mt-2 space-y-1">
                {result.matches.map((match) => (
                  <li key={`${match.identifier}-${match.student_id}`}>
                    <Link
                      href={`/students/${match.student_id}`}
                      className="font-semibold underline"
                    >
                      {[match.display_name, match.admission_no, match.class_section]
                        .filter(Boolean)
                        .join(" · ") || t("unnamed")}
                    </Link>{" "}
                    <span className="text-sm text-ink-muted">
                      ({t(`status.${statusKey(match.status)}`)})
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </Alert>
        ) : null}
      </div>
      <ApiErrorAlert error={error} />
    </div>
  );
}

function statusKey(status: string): "active" | "provisional" | "left" | "graduated" {
  return status === "provisional" || status === "left" || status === "graduated"
    ? status
    : "active";
}
