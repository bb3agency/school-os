"use client";

import { useTranslations } from "next-intl";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import * as z from "@/lib/zod";
import {
  CATEGORIES,
  KEYS,
  READ,
  RECORD,
  ifMatch,
  isUnavailable,
  optinSchema,
  useGuardianOptins,
  type CategoryState,
  type GuardianOptins,
} from "./data";

const UPDATE_GUARDIAN = "student.update_nonidentity";

const STATE_PILL: Record<CategoryState["status"], PillVariant> = {
  opted_in: "done",
  opted_out: "negative",
  not_asked: "sample",
};

export interface OptinGuardian {
  id: string;
  full_name: string;
  version: number;
  preferred_language?: "en" | "te" | null;
}

function today(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function CategoryRow({ state }: { state: CategoryState }) {
  const t = useTranslations("messaging.optins");
  return (
    <li className="flex flex-wrap items-center gap-2">
      <span className="min-w-28 font-semibold text-ink">{t(`categories.${state.category}`)}</span>
      <Pill variant={STATE_PILL[state.status]}>{t(`states.${state.status}`)}</Pill>
      {state.method ? (
        <span className="text-sm text-ink-muted">
          {t(`methods.${state.method}`)}
          {state.decided_on ? ` · ${formatDate(state.decided_on) ?? ""}` : ""}
        </span>
      ) : null}
      {state.stopped_by_parent ? (
        <span className="text-sm text-danger">{t("stoppedByParent")}</span>
      ) : null}
      {state.status === "opted_in" && !state.for_current_number ? (
        <span className="text-sm text-warning-ink">{t("numberChanged")}</span>
      ) : null}
    </li>
  );
}

function RecordDialog({ guardianId, data }: { guardianId: string; data: GuardianOptins }) {
  const t = useTranslations("messaging.optins");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("record")}
      triggerSize="sm"
      title={t("recordTitle")}
      description={t("recordDescription")}
      confirmLabel={t("save")}
      schema={optinSchema}
      invalidate={[KEYS.optins(guardianId)]}
      errorNamespace="messaging"
      submit={(input, key) =>
        unwrap(
          api.POST("/api/v1/guardians/{guardian_id}/optins", {
            params: { path: { guardian_id: guardianId } },
            headers: { "Idempotency-Key": key, "If-Match": ifMatch(data.version) },
            body: {
              category: input.category,
              status: input.choice === "staff_recorded_stop" ? "opted_out" : "opted_in",
              method: input.choice,
              decided_on: input.decided_on,
              evidence_document_id: input.evidence_document_id || null,
            },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <SelectField
            name="category"
            label={t("category")}
            error={errors.category}
            defaultValue="notices"
            options={CATEGORIES.map((c) => ({ value: c, label: t(`categories.${c}`) }))}
          />
          <SelectField
            name="choice"
            label={t("method")}
            error={errors.choice}
            defaultValue="in_person"
            options={(["in_person", "signed_form", "staff_recorded_stop"] as const).map((c) => ({
              value: c,
              label: t(`choices.${c}`),
            }))}
          />
          <TextField
            name="decided_on"
            type="date"
            label={t("decidedOn")}
            defaultValue={today()}
            max={today()}
            error={errors.decided_on}
          />
          <TextField
            name="evidence_document_id"
            label={t("evidence")}
            hint={t("evidenceHint")}
            autoComplete="off"
            spellCheck={false}
            error={errors.evidence_document_id}
          />
        </>
      )}
    </ActionDialog>
  );
}

function LanguageDialog({ studentId, guardian }: { studentId: string; guardian: OptinGuardian }) {
  const t = useTranslations("messaging.optins");
  const api = useBffClient("staff");
  const schema = z.object({ language: z.enum(["school", "en", "te"]) });
  return (
    <ActionDialog
      triggerLabel={t("language")}
      triggerSize="sm"
      title={t("languageTitle")}
      description={t("languageDescription")}
      confirmLabel={t("save")}
      schema={schema}
      invalidate={[KEYS.optins(guardian.id), ["staff", "students", studentId]]}
      submit={(input) =>
        unwrap(
          api.PATCH("/api/v1/students/{student_id}/guardians/{guardian_id}", {
            params: { path: { student_id: studentId, guardian_id: guardian.id } },
            headers: { "If-Match": ifMatch(guardian.version) },
            body: { preferred_language: input.language === "school" ? null : input.language },
          }),
        )
      }
    >
      {(errors) => (
        // The parent's own language choice (ADR-0044); staff screens stay English.
        <SelectField
          name="language"
          label={t("languageLabel")}
          error={errors.language}
          defaultValue={guardian.preferred_language ?? "school"}
          options={(["school", "en", "te"] as const).map((v) => ({
            value: v,
            label: t(`languages.${v}`),
          }))}
        />
      )}
    </ActionDialog>
  );
}

function GuardianBlock({
  studentId,
  guardian,
  canRecord,
  canSetLanguage,
}: {
  studentId: string;
  guardian: OptinGuardian;
  canRecord: boolean;
  canSetLanguage: boolean;
}) {
  const t = useTranslations("messaging.optins");
  const optins = useGuardianOptins(guardian.id);
  if (optins.status === "loading") return <p className="text-sm text-ink-muted">{t("loading")}</p>;
  if (optins.status !== "ready") return null;
  const data = optins.data;
  return (
    <section
      aria-label={guardian.full_name}
      className="space-y-2 border-t border-border-soft pt-3 first:border-t-0 first:pt-0"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold text-ink">
          {guardian.full_name}
          <span className="ml-2 text-sm font-normal text-ink-muted">
            {data.has_mobile ? t("mobileEnding", { last4: data.phone_last4 ?? "" }) : t("noMobile")}
          </span>
        </h3>
        <span className="flex flex-wrap gap-1">
          {canRecord && data.has_mobile ? (
            <RecordDialog guardianId={guardian.id} data={data} />
          ) : null}
          {canSetLanguage ? <LanguageDialog studentId={studentId} guardian={guardian} /> : null}
        </span>
      </div>
      <ul className="space-y-1">
        {data.categories.map((state) => (
          <CategoryRow key={state.category} state={state} />
        ))}
      </ul>
      <p className="text-sm text-ink-muted">
        {t("languageNow", { language: t(`languages.${data.effective_language}`) })}
      </p>
    </section>
  );
}

/**
 * WhatsApp opt-ins of a student's guardians (US-2202; FR-MSG-008; docs/17 §5.8): each
 * category's state and how it was given, "stopped by the parent" read-only, a form to record a
 * decision with its evidence, and the guardian's message language (ADR-0044 decision 4). Shown
 * only where messaging is offered (404 `module_unavailable` hides it).
 */
export function GuardianOptinsCard({
  studentId,
  guardians,
}: {
  studentId: string;
  guardians: readonly OptinGuardian[];
}) {
  const t = useTranslations("messaging.optins");
  const can = useStaffCan();
  const canSee = can([READ, RECORD]);
  const first = guardians[0];
  const probe = useGuardianOptins(first?.id ?? "", canSee && first !== undefined);
  if (!canSee || first === undefined || isUnavailable(probe) || probe.status === "error") {
    return null;
  }
  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-3">
        <Alert tone="info" title={t("ruleTitle")}>
          {t("ruleBody")}
        </Alert>
        {guardians.map((guardian) => (
          <GuardianBlock
            key={guardian.id}
            studentId={studentId}
            guardian={guardian}
            canRecord={can(RECORD)}
            canSetLanguage={can(UPDATE_GUARDIAN)}
          />
        ))}
      </div>
    </Card>
  );
}
