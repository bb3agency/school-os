"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console/DataTableV2";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextAreaField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { Pager, useCursorStack } from "@/features/students/paging";
import { Link, useRouter } from "@/i18n/navigation";
import { newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { KEYS, NOTICE_DRAFT, looksPersonal, useNotices, type Notice } from "./data";
import { NoticeStatusPill } from "./parts";

/** Start a notice from staff text (AI drafts both languages) or blank (write it yourself). */
function NewNoticeCard() {
  const t = useTranslations("notices.new");
  const api = useBffClient("staff");
  const router = useRouter();
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const personal = looksPersonal(text);

  async function start(source: "staff_text" | "blank", event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (source === "staff_text" && (!text.trim() || personal)) return;
    setPending(true);
    setError(undefined);
    try {
      const notice = await unwrap(
        api.POST("/api/v1/notices", {
          headers: { "Idempotency-Key": newIdempotencyKey() },
          body: source === "blank" ? { source } : { source, text: text.trim() },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: KEYS.notices });
      router.push(`/notices/${notice.id}`);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card title={t("title")} description={t("description")}>
      <form onSubmit={(event) => void start("staff_text", event)} noValidate className="space-y-3">
        <Alert tone="warning" title={t("privacyTitle")}>
          {t("privacyBody")}
        </Alert>
        <TextAreaField
          name="text"
          label={t("textLabel")}
          hint={t("textHint")}
          rows={4}
          maxLength={4000}
          value={text}
          onChange={(event) => setText(event.target.value)}
          error={personal ? t("personal") : undefined}
        />
        <div className="flex flex-wrap gap-3">
          <Button
            type="submit"
            variant="brand"
            disabled={pending || !text.trim() || personal}
            aria-disabled={pending || !text.trim() || personal || undefined}
          >
            {t("draftWithAi")}
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={pending}
            aria-disabled={pending || undefined}
            onClick={() => void start("blank")}
          >
            {t("blank")}
          </Button>
        </div>
        <p className="text-sm text-ink-muted">{t("fromCircularHint")}</p>
        <ApiErrorAlert error={error} namespace="notices" />
      </form>
    </Card>
  );
}

/**
 * Parent notices (US-1605, US-1606): drafts and approved notices, newest first, and a way to
 * start one. Notices are posted by staff in the existing parents' groups; SchoolOS never sends
 * them itself.
 */
export function NoticesScreen() {
  const t = useTranslations("notices");
  const telugu = useTeluguEnabled();
  const can = useStaffCan();
  const queryClient = useQueryClient();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(NOTICE_DRAFT);
  // Cursor paging (docs/09 §2): a page holds 50 notices, newest first.
  const pages = useCursorStack();
  const list = useNotices(allowed, pages.cursor);
  const rows: Loadable<readonly Notice[]> = !meLoaded
    ? { status: "loading" }
    : list.status === "ready"
      ? { status: "ready", data: list.data.data }
      : list;
  const nextCursor = list.status === "ready" ? list.data.next_cursor : null;

  const columns: ColumnV2<Notice>[] = [
    {
      key: "notice",
      header: t("columns.notice"),
      cell: (notice) => (
        <>
          <Link
            href={`/notices/${notice.id}`}
            className="font-semibold break-words text-primary underline underline-offset-4 hover:no-underline"
          >
            {notice.title_en || t("untitled")}
          </Link>
          {telugu && notice.title_te ? (
            <p lang="te" className="text-sm text-ink-muted">
              {notice.title_te}
            </p>
          ) : null}
        </>
      ),
    },
    {
      key: "status",
      header: t("columns.status"),
      cell: (notice) => <NoticeStatusPill status={notice.status} />,
    },
    {
      key: "updated",
      header: t("columns.updated"),
      className: "num whitespace-nowrap",
      cell: (notice) => <Value>{formatDateTime(notice.updated_at)}</Value>,
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader title={t("title")} description={t("description")} />
      {meLoaded && !allowed ? (
        <EmptyState icon="lock" title={t("noAccessTitle")} body={t("noAccessBody")} />
      ) : (
        <>
          <NewNoticeCard />
          <DataTableV2
            id="notices"
            caption={t("listTitle")}
            columns={columns}
            state={rows}
            rowKey={(notice) => notice.id}
            stacked
            emptyTitle={t("emptyTitle")}
            emptyBody={t("emptyBody")}
            onRetry={() => void queryClient.invalidateQueries({ queryKey: KEYS.notices })}
            footer={
              pages.hasPrevious || nextCursor ? (
                <Pager
                  label={t("pagesLabel")}
                  page={pages.page}
                  onPrevious={pages.hasPrevious ? pages.previous : undefined}
                  onNext={nextCursor ? () => pages.next(nextCursor) : undefined}
                />
              ) : undefined
            }
          />
        </>
      )}
    </div>
  );
}
