"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useRef, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { FileDropZone, fileInputClasses } from "@/features/imports/parts";
import { extensionOf, uploadDocument, type UploadProgress } from "@/features/imports/upload";
import { PERM, useStaffPermissions, type Permissions } from "@/features/students/me";
import { Pager, useCursorStack } from "@/features/students/paging";
import { ProblemAlert } from "@/features/students/ProblemAlert";
import { Link, useRouter } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatBytes, formatCount, formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import {
  MAX_PHOTO_BYTES,
  MAX_PHOTOS,
  PHOTO_EXTENSIONS,
  type BatchStatus,
  type ExtractionBatch,
} from "./types";

export const EXTRACTION_KEY = ["staff", "extraction"] as const;
const ACCEPT = ".jpg,.jpeg,.png,image/jpeg,image/png";

/** Workflow pills: being read = in progress, rows to check = review, all checked = done. */
const batchPill: Record<BatchStatus, PillVariant> = {
  queued: "progress",
  processing: "progress",
  review: "review",
  completed: "done",
  failed: "negative",
};

export function BatchStatusBadge({ status }: { status: BatchStatus }) {
  const t = useTranslations("extraction.batchStatus");
  return <Pill variant={batchPill[status]}>{t(status)}</Pill>;
}

export type PhotoProblem =
  "photosMissing" | "photosTooMany" | "photoType" | "photoPdf" | "photoTooLarge";

/** Checks before anything is sent (the API checks again). */
export function checkPhotos(files: readonly File[]): PhotoProblem | null {
  if (files.length === 0) return "photosMissing";
  if (files.length > MAX_PHOTOS) return "photosTooMany";
  for (const file of files) {
    const ext = extensionOf(file.name);
    if (ext === "pdf") return "photoPdf";
    if (!(PHOTO_EXTENSIONS as readonly string[]).includes(ext)) return "photoType";
    if (file.size > MAX_PHOTO_BYTES) return "photoTooLarge";
  }
  return null;
}

interface Progress {
  current: number;
  total: number;
  step: UploadProgress | null;
  starting: boolean;
}

/** US-402 AC1: upload register-page photos, then start reading them (POST /extraction-batches). */
export function UploadPhotos({ onStarted }: { onStarted: (batch: ExtractionBatch) => void }) {
  const t = useTranslations("extraction.upload");
  const ts = useTranslations("students");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const fileRef = useRef<HTMLInputElement>(null);
  const statusId = useId();
  const [problem, setProblem] = useState<PhotoProblem | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const busy = progress !== null;

  async function start(files: readonly File[]) {
    const ids: string[] = [];
    for (const [index, file] of files.entries()) {
      setProgress({ current: index + 1, total: files.length, step: null, starting: false });
      ids.push(
        await uploadDocument(api, file, "register_scan", {
          onProgress: (step) =>
            setProgress({ current: index + 1, total: files.length, step, starting: false }),
        }),
      );
    }
    setProgress({ current: files.length, total: files.length, step: null, starting: true });
    return unwrap(
      api.POST("/api/v1/extraction-batches", {
        headers: { "Idempotency-Key": newIdempotencyKey() },
        body: { document_ids: ids },
      }),
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const files = [...(fileRef.current?.files ?? [])];
    const found = checkPhotos(files);
    setProblem(found);
    setError(undefined);
    if (found) {
      fileRef.current?.focus();
      return;
    }
    start(files)
      .then((batch) => {
        form.reset();
        setProgress(null);
        onStarted(batch);
      })
      .catch((failure: unknown) => {
        setProgress(null);
        setError(failure);
      });
  }

  const statusText = (value: Progress) => {
    if (value.starting) return t("starting");
    const stage =
      value.step?.stage === "uploading" && value.step.percent !== undefined
        ? t("stagePercent", { percent: value.step.percent })
        : value.step
          ? t(`stage.${value.step.stage}`)
          : "";
    return t("photoOf", { current: value.current, total: value.total, stage });
  };

  return (
    <form noValidate onSubmit={onSubmit} className="space-y-4">
      <Field
        label={t("photos")}
        hint={t("photosHint", {
          max: MAX_PHOTOS,
          size: formatBytes(MAX_PHOTO_BYTES, locale) ?? "25 MB",
        })}
        error={
          problem === "photosTooMany"
            ? t("photosTooMany", { max: MAX_PHOTOS })
            : problem
              ? t(problem)
              : undefined
        }
      >
        {({ id, describedBy, invalid }) => (
          <FileDropZone title={t("dropTitle")} icon="camera" disabled={busy}>
            <input
              ref={fileRef}
              id={id}
              name="photos"
              type="file"
              multiple
              accept={ACCEPT}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
              disabled={busy}
              onChange={() => setProblem(null)}
              className={fileInputClasses}
            />
          </FileDropZone>
        )}
      </Field>
      <Alert tone="warning" title={ts("aadhaarWarningTitle")}>
        {t("aadhaarNote")}
      </Alert>
      <div id={statusId} role="status" aria-live="polite" className="min-h-6 space-y-2">
        {progress ? (
          <>
            <p className="text-sm font-medium">{statusText(progress)}</p>
            <progress
              max={progress.total}
              value={progress.starting ? progress.total : progress.current - 1}
              aria-label={t("progressLabel")}
              className="h-2 w-full accent-primary"
            />
          </>
        ) : null}
      </div>
      <ProblemAlert error={error} namespace={["extraction.errors", "imports.errors"]} />
      <div className="flex justify-end">
        <Button type="submit" disabled={busy} aria-describedby={statusId}>
          {busy ? t("working") : t("submit")}
        </Button>
      </div>
    </form>
  );
}

export interface RegisterPhotosViewProps {
  batches: Loadable<readonly ExtractionBatch[]>;
  permissions: Permissions;
  page: number;
  onNext?: (() => void) | undefined;
  onPrevious?: (() => void) | undefined;
  onStarted: (batch: ExtractionBatch) => void;
}

/** US-402 / FR-IMP-020..023: register photos read into a verification queue. */
export function RegisterPhotosView({
  batches,
  permissions,
  page,
  onNext,
  onPrevious,
  onStarted,
}: RegisterPhotosViewProps) {
  const t = useTranslations("extraction.list");
  const tn = useTranslations("school.nav");
  const locale = useLocale() as Locale;
  const count = (value: number) => formatCount(value, locale) ?? String(value);
  const canUpload = permissions.has(PERM.importRun) && permissions.has(PERM.documentUpload);

  const columns: Column<ExtractionBatch>[] = [
    {
      key: "started",
      header: t("colStarted"),
      cell: (row) => (
        <Link
          href={`/register-photos/${row.id}`}
          className="font-medium text-primary underline underline-offset-4"
        >
          {formatDateTime(row.created_at) ?? t("open")}
          <span className="sr-only">{t("openHint")}</span>
        </Link>
      ),
    },
    {
      key: "pages",
      header: t("colPages"),
      cell: (row) => (
        <span className="tabular-nums">
          {t("pagesDone", { done: count(row.pages_done), total: count(row.page_count) })}
        </span>
      ),
    },
    {
      key: "pending",
      header: t("colPending"),
      cell: (row) =>
        row.items_pending > 0 ? (
          <Pill variant="review">{count(row.items_pending)}</Pill>
        ) : (
          <span className="tabular-nums">{count(row.items_pending)}</span>
        ),
    },
    {
      key: "confirmed",
      header: t("colConfirmed"),
      cell: (row) => <span className="tabular-nums">{count(row.items_confirmed)}</span>,
    },
    {
      key: "withheld",
      header: t("colWithheld"),
      cell: (row) =>
        row.pages_withheld > 0 ? (
          <Pill variant="negative">{count(row.pages_withheld)}</Pill>
        ) : (
          count(row.pages_withheld)
        ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <BatchStatusBadge status={row.status} />,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {canUpload ? (
        <Card title={t("uploadTitle")} description={t("uploadDescription")}>
          <UploadPhotos onStarted={onStarted} />
        </Card>
      ) : null}
      <Card title={t("historyTitle")}>
        <div className="space-y-3">
          <DataTable
            caption={t("historyTitle")}
            captionHidden
            columns={columns}
            state={batches}
            rowKey={(row) => row.id}
            emptyTitle={t("emptyTitle")}
            emptyBody={t("emptyBody")}
          />
          <Pager label={t("pagesLabel")} page={page} onPrevious={onPrevious} onNext={onNext} />
        </div>
      </Card>
    </div>
  );
}

/** GET /extraction-batches (cursor). */
export function RegisterPhotosScreen() {
  const api = useBffClient("staff");
  const router = useRouter();
  const permissions = useStaffPermissions();
  const pages = useCursorStack();
  const query = { limit: 25, ...(pages.cursor ? { cursor: pages.cursor } : {}) };
  const list = useApiQuery([...EXTRACTION_KEY, "batches", query], () =>
    unwrap(api.GET("/api/v1/extraction-batches", { params: { query } })),
  );
  const batches: Loadable<readonly ExtractionBatch[]> =
    list.status === "ready" ? { status: "ready", data: list.data.data } : list;
  const next = list.status === "ready" ? list.data.next_cursor : null;
  return (
    <RegisterPhotosView
      batches={batches}
      permissions={permissions}
      page={pages.page}
      onNext={next ? () => pages.next(next) : undefined}
      onPrevious={pages.hasPrevious ? pages.previous : undefined}
      onStarted={(batch) => router.push(`/register-photos/${batch.id}`)}
    />
  );
}
