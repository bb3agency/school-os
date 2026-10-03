"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextAreaField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { Link, useRouter } from "@/i18n/navigation";
import { newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import { KEYS, NOTICE_DRAFT, looksPersonal, useNotices } from "./data";
import { LoadGate, NoticeStatusPill } from "./parts";

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
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(NOTICE_DRAFT);
  const list = useNotices(allowed);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {meLoaded && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <>
          <NewNoticeCard />
          <Card title={t("listTitle")}>
            <LoadGate data={meLoaded ? list : { status: "loading" }}>
              {(page) =>
                page.data.length === 0 ? (
                  <EmptyState icon="megaphone" title={t("emptyTitle")} body={t("emptyBody")} />
                ) : (
                  <TableScroll label={t("listTitle")}>
                    <Table>
                      <THead>
                        <Tr>
                          <Th>{t("columns.notice")}</Th>
                          <Th>{t("columns.status")}</Th>
                          <Th>{t("columns.updated")}</Th>
                        </Tr>
                      </THead>
                      <TBody>
                        {page.data.map((notice) => (
                          <Tr key={notice.id}>
                            <Td>
                              <Link
                                href={`/notices/${notice.id}`}
                                className="font-semibold text-primary underline underline-offset-4 hover:no-underline"
                              >
                                {notice.title_en || t("untitled")}
                              </Link>
                              {telugu && notice.title_te ? (
                                <p lang="te" className="text-sm text-ink-muted">
                                  {notice.title_te}
                                </p>
                              ) : null}
                            </Td>
                            <Td>
                              <NoticeStatusPill status={notice.status} />
                            </Td>
                            <Td>
                              <Value>{formatDateTime(notice.updated_at)}</Value>
                            </Td>
                          </Tr>
                        ))}
                      </TBody>
                    </Table>
                  </TableScroll>
                )
              }
            </LoadGate>
          </Card>
        </>
      )}
    </div>
  );
}
