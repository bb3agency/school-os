"use client";

import { useTranslations } from "next-intl";
import { useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Label } from "@/components/ui/Label";
import {
  UploadError,
  uploadDocument,
  extensionOf,
  type UploadProgress,
} from "@/features/imports/upload";
import { useBffClient } from "@/lib/bff/query";
import { translateOr } from "@/lib/i18n-dynamic";
import type { SheetIssue } from "./data";
import { ProblemList, SheetIssues } from "./parts";

const MAX_BYTES = 2 * 1024 * 1024;

export interface SheetPreview<E> {
  entries: E[];
  issues: SheetIssue[];
  issue_count: number;
  students: number;
}

/**
 * Import a register or marks sheet (FR-ATT-004, FR-MRK-004): the file is uploaded the documents
 * way (virus-checked, visible to the section only), read by the API into entries and problems,
 * and deleted there; nothing is saved until the person confirms the entries here.
 */
export function SheetImport<E, P extends SheetPreview<E>>({
  section,
  title,
  description,
  sample,
  preview,
  commit,
  summary,
  onSaved,
}: {
  section: string;
  title: string;
  description: ReactNode;
  /** The expected layout, as a small example table. */
  sample: ReactNode;
  preview: (documentId: string) => Promise<P>;
  commit: (entries: E[]) => Promise<{ written: number }>;
  summary: (sheet: P) => string;
  onSaved: () => void | Promise<void>;
}) {
  const t = useTranslations("insights.sheet");
  const api = useBffClient("staff");
  const fileRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const [sheet, setSheet] = useState<P | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const busy = progress !== null || saving;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = fileRef.current?.files?.[0];
    setProblem(null);
    setError(undefined);
    setSheet(null);
    setSaved(null);
    if (!file) return setProblem("fileRequired");
    if (!["xlsx", "csv"].includes(extensionOf(file.name))) return setProblem("fileType");
    if (file.size > MAX_BYTES) return setProblem("fileTooLarge");
    try {
      const documentId = await uploadDocument(api, file, "import_file", {
        onProgress: setProgress,
        acl: [{ principal_type: "section", principal_ref: section }],
      });
      setSheet(await preview(documentId));
    } catch (failure) {
      if (failure instanceof UploadError) setProblem(`upload.${failure.code}`);
      else setError(failure);
    } finally {
      setProgress(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function confirm() {
    if (!sheet) return;
    setSaving(true);
    setError(undefined);
    try {
      const out = await commit(sheet.entries);
      setSaved(out.written);
      setSheet(null);
      await onSaved();
    } catch (failure) {
      setError(failure);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title={title} description={description}>
      <div className="space-y-4">
        <details className="text-sm text-ink-muted">
          <summary className="cursor-pointer text-primary underline underline-offset-4">
            {t("sampleTitle")}
          </summary>
          <div className="mt-2">{sample}</div>
        </details>
        <Alert tone="info">{t("aadhaarWarning")}</Alert>
        <form onSubmit={(event) => void onSubmit(event)} noValidate className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor={inputId}>{t("file")}</Label>
            <input
              id={inputId}
              ref={fileRef}
              type="file"
              accept=".xlsx,.csv"
              className="block w-full text-sm text-ink file:mr-3 file:min-h-10 file:rounded-md file:border-0 file:bg-surface-muted file:px-3 file:text-ink"
            />
          </div>
          <Button type="submit" disabled={busy || !section} aria-disabled={busy || undefined}>
            {t("read")}
          </Button>
          {progress ? (
            <p className="text-sm text-ink-muted" role="status">
              {t(`stage.${progress.stage}`)}
            </p>
          ) : null}
        </form>
        {problem ? (
          <Alert tone="danger" live>
            {translateOr(t, `fileProblem.${problem}`, "fileProblem.fileType")}
          </Alert>
        ) : null}
        <ApiErrorAlert error={error} />
        <ProblemList error={error} />
        {sheet ? (
          <div className="space-y-3" aria-live="polite">
            <p className="text-sm text-ink">{summary(sheet)}</p>
            <SheetIssues issues={sheet.issues} total={sheet.issue_count} />
            {sheet.issue_count === 0 && sheet.entries.length > 0 ? (
              <Button
                onClick={() => void confirm()}
                disabled={saving}
                aria-disabled={saving || undefined}
              >
                {t("confirm", { count: sheet.entries.length })}
              </Button>
            ) : sheet.issue_count === 0 ? (
              <p className="text-sm text-ink-muted">{t("nothingToAdd")}</p>
            ) : (
              <p className="text-sm text-ink-muted">{t("fixFirst")}</p>
            )}
          </div>
        ) : null}
        {saved !== null ? (
          <Alert tone="success" live>
            {t("saved", { count: saved })}
          </Alert>
        ) : null}
      </div>
    </Card>
  );
}
