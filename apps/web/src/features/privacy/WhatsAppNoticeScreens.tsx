"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useRef } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import { useRouter } from "@/i18n/navigation";
import { newIdempotencyKey, toLoadable, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatCount, formatDate } from "@/lib/format";
import { READINESS_KEYS, readinessApi, type NoticeDelivery } from "./readiness-api";
import { PRIVACY_KEYS, PRIVACY_MANAGE, PRIVACY_READ } from "./types";

/** Every permission "Send on WhatsApp" needs (the API checks them again). */
export const WHATSAPP_PERMS = [PRIVACY_MANAGE, "notice.draft", "notice.approve", "message.send"];

/**
 * "Send this privacy notice on WhatsApp" (FP-13; FR-DPDP-061..064): makes the parent notice
 * from the parents' privacy notice, shows who would get it and the Meta cost, and sends it on
 * confirmation. Not legal advice.
 */
export function WhatsAppSendScreen() {
  const t = useTranslations("privacy.whatsapp");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const router = useRouter();
  const queryClient = useQueryClient();
  const mePending = useStaffMeQuery().isPending;
  const allowed = WHATSAPP_PERMS.every((permission) => can(permission));
  const sendKey = useRef(newIdempotencyKey());

  const preview = useMutation({
    mutationFn: (parentNoticeId: string | undefined) =>
      readinessApi.whatsappPreview(api, parentNoticeId ? { parent_notice_id: parentNoticeId } : {}),
  });
  const send = useMutation({
    mutationFn: () => {
      const data = preview.data;
      if (!data) throw new Error("preview first");
      return readinessApi.whatsappSend(
        api,
        {
          parent_notice_id: data.parent_notice_id,
          preview_token: data.preview.preview_token,
          section_ids: data.preview.section_ids,
        },
        sendKey.current,
      );
    },
    onSuccess: async (out) => {
      await queryClient.invalidateQueries({ queryKey: PRIVACY_KEYS.notices });
      await queryClient.invalidateQueries({ queryKey: READINESS_KEYS.readiness });
      router.push(`/privacy/notices/${out.id}/delivery`);
    },
  });

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[
        { label: tn("home"), href: "/" },
        { label: t("notices"), href: "/privacy/notices" },
        { label: t("title") },
      ]}
    />
  );
  if (!mePending && !allowed) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }
  const data = preview.data;
  const skipped = data
    ? Object.values(data.preview.skipped).reduce((sum, value) => sum + value, 0)
    : 0;
  return (
    <div className="space-y-5">
      {header}
      <p className="text-sm text-ink-muted">{t("notLegalAdvice")}</p>
      {!data ? (
        <Card className="space-y-3 p-4">
          <p>{t("intro")}</p>
          <ApiErrorAlert error={preview.error} namespace="privacy" />
          <Button onClick={() => preview.mutate(undefined)} disabled={preview.isPending}>
            {preview.isPending ? t("preparing") : t("prepare")}
          </Button>
        </Card>
      ) : (
        <Card className="space-y-4 p-4">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold text-ink">{data.title}</h2>
            <p className="text-sm whitespace-pre-line">{data.body}</p>
          </div>
          <dl className="grid gap-2 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-ink-muted">{t("recipients")}</dt>
              <dd className="num font-semibold">{formatCount(data.preview.recipients, "en")}</dd>
            </div>
            <div>
              <dt className="text-ink-muted">{t("leftOut")}</dt>
              <dd className="num font-semibold">{formatCount(skipped, "en")}</dd>
            </div>
            <div>
              <dt className="text-ink-muted">{t("cost")}</dt>
              <dd className="num font-semibold">₹{data.preview.estimated_cost_inr}</dd>
            </div>
          </dl>
          <p className="text-sm text-ink-muted">{t("leftOutHint")}</p>
          {data.preview.blocked_reason ? (
            <Alert tone="warning" title={t("blockedTitle")}>
              {t("blockedBody")}
            </Alert>
          ) : null}
          <ApiErrorAlert error={send.error} namespace="privacy" />
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => send.mutate()}
              disabled={
                send.isPending ||
                data.preview.blocked_reason !== null ||
                data.preview.recipients === 0
              }
            >
              {send.isPending ? t("sending") : t("send", { count: data.preview.recipients })}
            </Button>
            <Button
              variant="secondary"
              onClick={() => preview.mutate(data.parent_notice_id)}
              disabled={preview.isPending}
            >
              {t("refresh")}
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}

type MissedRow = NoticeDelivery["not_reached"][number];

/**
 * Who the privacy notice sent on WhatsApp reached (FP-13; FR-DPDP-065, FR-DPDP-066): guardians
 * by delivery status, and the students to give a paper copy to (names only of students you may
 * see). Printable.
 */
export function NoticeDeliveryScreen({ noticeId }: { noticeId: string }) {
  const t = useTranslations("privacy.whatsapp.delivery");
  const tw = useTranslations("privacy.whatsapp");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const readable = can(PRIVACY_READ);
  const delivery = useQuery({
    queryKey: READINESS_KEYS.delivery(noticeId),
    queryFn: () => readinessApi.delivery(api, noticeId),
    enabled: readable,
    retry: false,
  });
  const data = delivery.data;
  const columns: ColumnV2<MissedRow>[] = [
    {
      key: "name",
      stack: "title",
      header: t("colStudent"),
      cell: (row) => <Value>{row.display_name}</Value>,
    },
    {
      key: "adm",
      header: t("colAdmission"),
      cell: (row) => (
        <span className="num">
          <Value>{row.admission_no}</Value>
        </span>
      ),
    },
    { key: "class", header: t("colClass"), cell: (row) => <Value>{row.class_section}</Value> },
  ];
  const count = (key: string) => formatCount(data?.guardians[key] ?? 0, "en") ?? "0";
  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={
          data
            ? t("description", { date: formatDate(data.notice.issued_on) ?? data.notice.issued_on })
            : undefined
        }
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: tw("notices"), href: "/privacy/notices" },
          { label: t("title") },
        ]}
        actions={
          data ? (
            <Button variant="secondary" onClick={() => window.print()}>
              {t("print")}
            </Button>
          ) : null
        }
      />
      <p className="text-sm text-ink-muted">{tw("notLegalAdvice")}</p>
      {delivery.isPending && readable ? <LoadingState label={t("loading")} rows={3} /> : null}
      <ApiErrorAlert error={delivery.error} namespace="privacy" />
      {data ? (
        <>
          <dl className="grid gap-3 text-sm sm:grid-cols-4">
            {(["read", "delivered", "waiting", "notReached"] as const).map((key) => (
              <Card key={key} className="p-3">
                <dt className="text-ink-muted">{t(`counts.${key}`)}</dt>
                <dd className="num text-xl font-semibold">
                  {key === "waiting"
                    ? formatCount((data.guardians.sent ?? 0) + (data.guardians.queued ?? 0), "en")
                    : key === "notReached"
                      ? formatCount(data.students_not_reached, "en")
                      : count(key)}
                </dd>
              </Card>
            ))}
          </dl>
          <p className="text-sm text-ink-muted">
            {t("guardiansNote", {
              failed: count("failed"),
              leftOut: count("left_out"),
            })}
          </p>
          <section aria-labelledby="notice-not-reached" className="space-y-3">
            <h2 id="notice-not-reached" className="text-lg font-semibold text-ink">
              {t("notReachedTitle")}
            </h2>
            {data.hidden > 0 ? (
              <p className="text-sm text-ink-muted">{t("hidden", { count: data.hidden })}</p>
            ) : null}
            <DataTableV2
              id="notice-not-reached-table"
              stacked
              caption={t("notReachedTitle")}
              captionHidden
              columns={columns}
              state={toLoadable({ ...delivery, data: data.not_reached })}
              rowKey={(row) => row.student_id}
              emptyTitle={t("emptyTitle")}
              emptyBody={t("emptyBody")}
              onRetry={() => void delivery.refetch()}
            />
          </section>
        </>
      ) : null}
      <ButtonLink href="/privacy/notices" variant="secondary" size="sm">
        {tw("backToNotices")}
      </ButtonLink>
    </div>
  );
}
