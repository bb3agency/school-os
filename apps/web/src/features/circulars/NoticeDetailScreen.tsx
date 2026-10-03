"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useEffect, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { Link } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import { zodErrorKeys } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
import {
  KEYS,
  NOTICE_APPROVE,
  NOTICE_SLOW_MS,
  ifMatch,
  isNoticeDrafting,
  looksPersonal,
  noticeComplete,
  noticeEnglishSchema,
  noticeSchema,
  noticeText,
  startDownload,
  useNotice,
  type Notice,
} from "./data";
import { AiNote, LoadGate, NoticeStatusPill } from "./parts";

type Field = "title_en" | "body_en" | "title_te" | "body_te";
const FIELDS: readonly Field[] = ["title_en", "body_en", "title_te", "body_te"];
/** ADR-0036: while Telugu is switched off a notice is edited, checked and sent in English. */
const ENGLISH_FIELDS: readonly Field[] = ["title_en", "body_en"];

function Editor({ notice, onSaved }: { notice: Notice; onSaved: () => Promise<void> }) {
  const t = useTranslations("notices.editor");
  const tv = useTranslations("validation");
  const can = useStaffCan();
  const api = useBffClient("staff");
  const telugu = useTeluguEnabled();
  const fields = telugu ? FIELDS : ENGLISH_FIELDS;
  const [values, setValues] = useState<Record<Field, string>>({
    title_en: notice.title_en,
    body_en: notice.body_en,
    title_te: notice.title_te,
    body_te: notice.body_te,
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(undefined);
  const [pending, setPending] = useState(false);
  const changed = fields.some((f) => values[f] !== notice[f]);
  const personal = fields.filter((f) => looksPersonal(values[f]));

  async function save(event?: FormEvent<HTMLFormElement>): Promise<Notice | null> {
    event?.preventDefault();
    const parsed = (telugu ? noticeSchema : noticeEnglishSchema).safeParse(values);
    if (!parsed.success) {
      setErrors(zodErrorKeys(parsed.error));
      return null;
    }
    setErrors({});
    setError(undefined);
    if (!changed) return notice;
    setPending(true);
    try {
      const saved = await unwrap(
        api.PATCH("/api/v1/notices/{notice_id}", {
          params: { path: { notice_id: notice.id } },
          headers: { "If-Match": ifMatch(notice.version) },
          body: parsed.data,
        }),
      );
      await onSaved();
      return saved;
    } catch (failure) {
      setError(failure);
      return null;
    } finally {
      setPending(false);
    }
  }

  async function approve() {
    const saved = await save();
    if (!saved) return;
    setPending(true);
    try {
      await unwrap(
        api.POST("/api/v1/notices/{notice_id}/approve", {
          params: { path: { notice_id: notice.id } },
          headers: { "If-Match": ifMatch(saved.version) },
          body: {},
        }),
      );
      await onSaved();
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const fieldError = (name: Field) =>
    personal.includes(name)
      ? t("personal")
      : errors[name]
        ? translateOr(tv, errors[name] ?? "invalid", "invalid")
        : undefined;
  const set = (name: Field) => (event: { target: { value: string } }) =>
    setValues({ ...values, [name]: event.target.value });
  const complete = noticeComplete(values, { telugu });

  return (
    <form onSubmit={(event) => void save(event)} noValidate className="space-y-4">
      <div className={telugu ? "grid gap-4 lg:grid-cols-2" : "max-w-3xl"}>
        <fieldset className="space-y-3">
          {telugu ? <legend className="font-semibold text-ink">{t("english")}</legend> : null}
          <TextField
            label={t("title")}
            lang="en"
            maxLength={120}
            value={values.title_en}
            onChange={set("title_en")}
            error={fieldError("title_en")}
          />
          <TextAreaField
            label={t("body")}
            lang="en"
            rows={7}
            maxLength={1500}
            value={values.body_en}
            onChange={set("body_en")}
            error={fieldError("body_en")}
          />
        </fieldset>
        {telugu ? (
          <fieldset className="space-y-3">
            <legend className="font-semibold text-ink">{t("telugu")}</legend>
            <TextField
              label={t("title")}
              lang="te"
              maxLength={120}
              value={values.title_te}
              onChange={set("title_te")}
              error={fieldError("title_te")}
            />
            <TextAreaField
              label={t("body")}
              lang="te"
              rows={7}
              maxLength={1500}
              value={values.body_te}
              onChange={set("body_te")}
              error={fieldError("body_te")}
              className="leading-loose"
            />
          </fieldset>
        ) : null}
      </div>
      <p className="text-sm text-ink-muted">{t("noPersonal")}</p>
      <div className="flex flex-wrap gap-3">
        <Button
          type="submit"
          variant="secondary"
          disabled={pending || !changed}
          aria-disabled={pending || !changed || undefined}
        >
          {t("save")}
        </Button>
        {can(NOTICE_APPROVE) ? (
          <Button
            type="button"
            onClick={() => void approve()}
            disabled={pending || !complete || personal.length > 0}
            aria-disabled={pending || !complete || personal.length > 0 || undefined}
          >
            {t("approve")}
          </Button>
        ) : (
          <p className="text-sm text-ink-muted">{t("approverNeeded")}</p>
        )}
      </div>
      {!complete ? <p className="text-sm text-ink-muted">{t("incomplete")}</p> : null}
      <ApiErrorAlert error={error} namespace="notices" />
    </form>
  );
}

/** True once `since` (an ISO time) is more than NOTICE_SLOW_MS ago. */
function useSlow(since: string): boolean {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const left = Date.parse(since) + NOTICE_SLOW_MS - Date.now();
    const timer = setTimeout(() => setSlow(true), Math.max(left, 0));
    return () => clearTimeout(timer);
  }, [since]);
  return slow;
}

/**
 * The AI drafts the notice in the background (FR-NOTICE-003); the page asks again with
 * backoff until it is a draft or the AI could not draft it. Leaving the page is fine.
 */
function Drafting({ notice }: { notice: Notice }) {
  const t = useTranslations("notices.drafting");
  const slow = useSlow(notice.updated_at);
  const telugu = useTeluguEnabled();
  return (
    <div className="space-y-4">
      <div role="status" aria-live="polite" className="space-y-1">
        <p className="font-semibold text-ink">{t("title")}</p>
        <p className="text-sm text-ink-muted">{slow ? t("slow") : t("body")}</p>
      </div>
      <div className={telugu ? "grid gap-4 lg:grid-cols-2" : "max-w-3xl"} aria-hidden="true">
        {(telugu ? ["en", "te"] : ["en"]).map((language) => (
          <div key={language} className="space-y-2">
            <Skeleton className="h-9 rounded-md" />
            <Skeleton className="h-40 rounded-md" />
          </div>
        ))}
      </div>
    </div>
  );
}

/** The AI could not draft the notice: say why, and offer to try again (or write it below). */
function DraftFailed({ notice, onChanged }: { notice: Notice; onChanged: () => Promise<void> }) {
  const t = useTranslations("notices.draftFailed");
  const tr = useTranslations("notices.draftError");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);

  async function retry() {
    setPending(true);
    setError(undefined);
    try {
      await unwrap(
        api.POST("/api/v1/notices/{notice_id}/draft", {
          params: { path: { notice_id: notice.id } },
          headers: { "If-Match": ifMatch(notice.version) },
          body: {},
        }),
      );
      await onChanged();
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-3">
      <Alert tone="warning" title={t("title")}>
        <p>{translateOr(tr, notice.draft_error ?? "other", "other")}</p>
        <p>{t("body")}</p>
      </Alert>
      <Button
        variant="secondary"
        disabled={pending}
        aria-disabled={pending || undefined}
        onClick={() => void retry()}
      >
        {pending ? tc("working") : t("retry")}
      </Button>
      <ApiErrorAlert error={error} namespace="notices" />
    </div>
  );
}

/** An approved notice: the text to copy, the printable page and the downloads. */
function Approved({ notice, onChanged }: { notice: Notice; onChanged: () => Promise<void> }) {
  const t = useTranslations("notices.approved");
  const api = useBffClient("staff");
  const telugu = useTeluguEnabled();
  const [copied, setCopied] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);

  async function copy() {
    setCopied(false);
    try {
      await navigator.clipboard.writeText(noticeText(notice, { telugu }));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  async function download(format: "pdf" | "png") {
    setPending(true);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/notices/{notice_id}/download-url", {
          params: { path: { notice_id: notice.id }, query: { format } },
        }),
      );
      startDownload(link.url);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  async function render() {
    setPending(true);
    setError(undefined);
    try {
      await unwrap(
        api.POST("/api/v1/notices/{notice_id}/render", {
          params: { path: { notice_id: notice.id } },
          headers: { "If-Match": ifMatch(notice.version) },
          body: {},
        }),
      );
      await onChanged();
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-4">
      <article
        className="space-y-4 rounded-lg border border-border bg-surface p-5 print:border-0 print:p-0"
        aria-label={t("preview")}
      >
        <section lang="en" className="space-y-2">
          <h2 className="text-xl font-semibold text-ink">{notice.title_en}</h2>
          <p className="whitespace-pre-wrap text-ink">{notice.body_en}</p>
        </section>
        {telugu ? (
          <>
            <hr className="border-border" />
            <section lang="te" className="space-y-2">
              <h2 className="text-xl leading-loose font-semibold text-ink">{notice.title_te}</h2>
              <p className="leading-loose whitespace-pre-wrap text-ink">{notice.body_te}</p>
            </section>
          </>
        ) : null}
      </article>
      <div className="flex flex-wrap gap-3" data-print="hide">
        <Button onClick={() => void copy()}>{t("copy")}</Button>
        <Button variant="secondary" onClick={() => window.print()}>
          {t("print")}
        </Button>
        {notice.files_available ? (
          <>
            <Button
              variant="secondary"
              disabled={pending}
              aria-disabled={pending || undefined}
              onClick={() => void download("pdf")}
            >
              {t("downloadPdf")}
            </Button>
            <Button
              variant="secondary"
              disabled={pending}
              aria-disabled={pending || undefined}
              onClick={() => void download("png")}
            >
              {t("downloadImage")}
            </Button>
          </>
        ) : notice.render_status === "queued" ? (
          <p role="status" className="text-sm text-ink-muted">
            {t("rendering")}
          </p>
        ) : (
          <Button
            variant="secondary"
            disabled={pending}
            aria-disabled={pending || undefined}
            onClick={() => void render()}
          >
            {t("renderAgain")}
          </Button>
        )}
      </div>
      {copied ? (
        <Alert tone="success" live>
          {t("copied")}
        </Alert>
      ) : null}
      {notice.render_status === "failed" ? <Alert tone="warning">{t("renderFailed")}</Alert> : null}
      <p className="text-sm text-ink-muted" data-print="hide">
        {t("postNote")}
      </p>
      <ApiErrorAlert error={error} namespace="notices" />
    </div>
  );
}

/**
 * One parent notice (US-1605, US-1606): while the AI drafts it in the background the page shows
 * progress and asks again (FR-NOTICE-003); if the AI could not draft it, try again or write it
 * yourself. Edit the English (and, while Telugu is switched on, Telugu; ADR-0036) text of a
 * draft (an AI draft is marked as such),
 * approve it (`notice.approve`), then copy the text for the parents' groups, print it on A4 or
 * download the PDF or image. Approved notices cannot change.
 */
export function NoticeDetailScreen({ noticeId }: { noticeId: string }) {
  const t = useTranslations("notices");
  const tn = useTranslations("school.nav");
  const tr = useTranslations("notices.draftError");
  const queryClient = useQueryClient();
  const detail = useNotice(noticeId, true);

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: KEYS.notices });
  }

  return (
    <div className="space-y-6">
      <LoadGate data={detail}>
        {(notice) => (
          <>
            <PageHeader
              title={notice.title_en || t("untitled")}
              breadcrumb={[
                { label: tn("home"), href: "/" },
                { label: t("title"), href: "/notices" },
                { label: notice.title_en || t("untitled") },
              ]}
              badge={<NoticeStatusPill status={notice.status} />}
            />
            {notice.document_id ? (
              <p className="text-sm" data-print="hide">
                <Link
                  href={`/circulars/${notice.document_id}`}
                  className="text-primary underline underline-offset-4"
                >
                  {t("fromCircular")}
                </Link>
              </p>
            ) : null}
            {notice.ai_drafted ? <AiNote>{t("aiDrafted")}</AiNote> : null}
            {notice.status === "draft_failed" ? (
              <DraftFailed notice={notice} onChanged={refresh} />
            ) : notice.draft_error && !isNoticeDrafting(notice.status) ? (
              <Alert tone="info" title={t("draftErrorTitle")}>
                {translateOr(tr, notice.draft_error, "other")}
              </Alert>
            ) : null}
            <Card
              title={
                notice.status === "approved"
                  ? t("approvedTitle")
                  : isNoticeDrafting(notice.status)
                    ? t("draftingTitle")
                    : t("editTitle")
              }
            >
              {notice.status === "approved" ? (
                <Approved notice={notice} onChanged={refresh} />
              ) : isNoticeDrafting(notice.status) ? (
                <Drafting notice={notice} />
              ) : (
                <Editor key={notice.version} notice={notice} onSaved={refresh} />
              )}
            </Card>
            {notice.approved_at ? (
              <p className="text-sm text-ink-muted">
                {t("approvedBy", {
                  name: notice.approved_by?.display_name ?? t("formerStaff"),
                  when: formatDateTime(notice.approved_at) ?? "",
                })}
              </p>
            ) : null}
          </>
        )}
      </LoadGate>
    </div>
  );
}
