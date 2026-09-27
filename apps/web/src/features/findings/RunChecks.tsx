"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useRef, type ReactNode } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/Select";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDateTime } from "@/lib/format";
import { formList } from "@/lib/forms";
import { uuid } from "@/lib/validation";
import { DQ_KEYS, useProfiles, useSectionOptions } from "./data";
import type { DqRun, RunStatus } from "./types";

/** Poll a queued/running check: 2 s, 3 s, 4.5 s … at most every 15 s (slow office links). */
export function runPollDelay(attempt: number): number {
  return Math.min(Math.round(2000 * 1.5 ** Math.max(0, attempt)), 15_000);
}

export function isRunFinished(status: RunStatus): boolean {
  return status === "completed" || status === "failed";
}

function stat(run: DqRun, name: string): number | null {
  const value = run.stats?.[name];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Status of one check run (POST /dq/runs answers 202; GET /dq/runs/{id} until it finishes).
 * Announced politely to screen readers; when it finishes the findings and summary reload.
 */
export function RunProgress({ runId, initial }: { runId: string; initial?: DqRun }) {
  const t = useTranslations("findings.run");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: DQ_KEYS.run(runId),
    queryFn: () =>
      unwrap(api.GET("/api/v1/dq/runs/{run_id}", { params: { path: { run_id: runId } } })),
    ...(initial ? { initialData: initial } : {}),
    refetchInterval: (current) => {
      const data = current.state.data;
      if (data && isRunFinished(data.status)) return false;
      if (current.state.status === "error") return false;
      return runPollDelay(current.state.dataUpdateCount);
    },
    retry: 1,
  });
  const run = query.data;
  const finished = run ? isRunFinished(run.status) : false;
  const announced = useRef(false);
  useEffect(() => {
    if (!finished || announced.current) return;
    announced.current = true;
    void queryClient.invalidateQueries({ queryKey: DQ_KEYS.findings });
    void queryClient.invalidateQueries({ queryKey: DQ_KEYS.summary });
  }, [finished, queryClient]);

  const count = (value: number | null) => formatCount(value ?? 0, locale) ?? "0";

  let body: ReactNode;
  if (!run) {
    body = query.isError ? (
      <Alert tone="danger" title={t("loadErrorTitle")}>
        {t("loadErrorBody")}
      </Alert>
    ) : (
      <p>{t("starting")}</p>
    );
  } else if (run.status === "queued" || run.status === "running") {
    body = (
      <Alert tone="info" title={t(run.status === "queued" ? "queuedTitle" : "runningTitle")}>
        <p>{t("backgroundNote")}</p>
      </Alert>
    );
  } else if (run.status === "failed") {
    body = (
      <Alert tone="danger" title={t("failedTitle")}>
        <p>{t("failedBody")}</p>
        {run.error_code ? (
          <p className="mt-1 text-xs">{t("errorCode", { code: run.error_code })}</p>
        ) : null}
      </Alert>
    );
  } else {
    body = (
      <Alert tone="success" title={t("completedTitle")}>
        <p>
          {t("completedBody", {
            students: count(stat(run, "students")),
            blockers: stat(run, "blockers") ?? 0,
            warnings: stat(run, "warnings") ?? 0,
          })}
        </p>
        <p className="mt-1">
          {t("changes", {
            new: stat(run, "new") ?? 0,
            reopened: stat(run, "reopened") ?? 0,
            cleared: stat(run, "cleared") ?? 0,
          })}
        </p>
        {run.finished_at ? (
          <p className="mt-1 text-xs">
            {t("finishedAt", { time: formatDateTime(run.finished_at) ?? "" })}
          </p>
        ) : null}
      </Alert>
    );
  }
  return (
    <div role="status" aria-live="polite" aria-atomic="true" className="space-y-3">
      {body}
    </div>
  );
}

const runSchema = z.object({
  profile_key: z
    .string()
    .trim()
    .transform((value) => (value === "" ? null : value)),
  section_ids: z.array(uuid).max(100, { error: "invalid" }),
});

/**
 * "Check now" (FR-DQ-002, US-501): pick sections (none = every student you can see) and,
 * optionally, an export profile such as CISCE registration. Small scopes finish at once; big
 * ones are queued and the bell says when they are done.
 */
export function RunChecksDialog({ defaultProfile }: { defaultProfile?: string | null }) {
  const t = useTranslations("findings.run");
  const tc = useTranslations("common");
  const locale = useLocale();
  const api = useBffClient("staff");
  const profiles = useProfiles();
  const { sections } = useSectionOptions();
  const byClass = new Map<string, typeof sections>();
  for (const section of sections) {
    const list = byClass.get(section.classLabel) ?? [];
    byClass.set(section.classLabel, [...list, section]);
  }
  return (
    <ActionDialog
      triggerLabel={t("open")}
      triggerVariant="primary"
      title={t("title")}
      description={t("description")}
      confirmLabel={t("start")}
      schema={runSchema}
      extra={(form) => ({ section_ids: formList(form, "section_ids") })}
      errorNamespace="findings"
      submit={(data, key) =>
        unwrap(
          api.POST("/api/v1/dq/runs", {
            headers: { "Idempotency-Key": key },
            body: {
              scope: data.section_ids.length > 0 ? { section_ids: data.section_ids } : {},
              profile_key: data.profile_key,
            },
          }),
        )
      }
      renderResult={(run, close) => (
        <>
          <RunProgress runId={run.id} initial={run} />
          <div className="flex flex-wrap justify-end gap-2">
            <Link
              href={`/findings/runs/${run.id}`}
              className="self-center text-sm text-primary underline"
            >
              {t("openRun")}
            </Link>
            <Button variant="secondary" onClick={close}>
              {tc("close")}
            </Button>
          </div>
        </>
      )}
    >
      {(errors) => (
        <>
          <SelectField
            name="profile_key"
            label={t("profile")}
            hint={t("profileHint")}
            error={errors.profile_key}
            defaultValue={defaultProfile ?? ""}
            options={[
              { value: "", label: t("noProfile") },
              ...(profiles.data ?? []).map((profile) => ({
                value: profile.key,
                label: locale === "te" && profile.label_te ? profile.label_te : profile.label_en,
              })),
            ]}
          />
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-ink">{t("sections")}</legend>
            <p className="text-sm text-ink-muted">{t("sectionsHint")}</p>
            {sections.length === 0 ? (
              <p className="text-sm text-ink-muted">{t("noSections")}</p>
            ) : (
              [...byClass.entries()].map(([classLabel, list]) => (
                <div key={classLabel || "none"} className="flex flex-wrap gap-x-4 gap-y-1">
                  {list.map((section) => (
                    <label
                      key={section.id}
                      className="inline-flex min-h-8 items-center gap-2 text-sm"
                    >
                      <input
                        type="checkbox"
                        name="section_ids"
                        value={section.id}
                        className="size-4"
                      />
                      {section.label}
                    </label>
                  ))}
                </div>
              ))
            )}
          </fieldset>
        </>
      )}
    </ActionDialog>
  );
}
