"use client";

import { useTranslations } from "next-intl";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Badge, Pill } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextAreaField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Link } from "@/i18n/navigation";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import { FEE_PERMISSIONS, KEYS, REASON_MAX, REASON_MIN, type ApprovalItem } from "./data";
import { Amount, FeesGate, NoAccess } from "./parts";

const rejectSchema = z.object({
  reason: z
    .string()
    .trim()
    .min(1, { error: "required" })
    .min(REASON_MIN, { error: "reasonTooShort" })
    .max(REASON_MAX, { error: "tooLong" }),
});
const approveSchema = z.object({
  note: z
    .string()
    .trim()
    .max(REASON_MAX, { error: "tooLong" })
    .transform((v) => (v === "" ? null : v)),
});

const PLURAL = {
  concession: "concessions",
  adjustment: "adjustments",
  cancellation: "cancellations",
  refund: "refunds",
} as const;

function Item({ item }: { item: ApprovalItem }) {
  const t = useTranslations("fees.approvals");
  const tf = useTranslations("fees");
  const api = useBffClient("staff");
  const invalidate = [KEYS.all] as const;
  const kind = PLURAL[item.kind];
  const decide = (action: "approve" | "reject" | "withdraw", body: object | undefined) =>
    unwrap(
      api.POST(
        `/api/v1/fees/${kind}/{${item.kind}_id}/${action}` as "/api/v1/fees/concessions/{concession_id}/approve",
        {
          params: { path: { [`${item.kind}_id`]: item.id } as { concession_id: string } },
          headers: { "If-Match": `W/"${item.version}"` },
          body: body as { note?: string | null },
        },
      ),
    );
  const who = `${tf(`approvalKinds.${item.kind}`)}, ${item.student_name ?? t("student")}`;
  return (
    <li className="grid gap-3 py-4 first:pt-0 last:pb-0 sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-x-6">
      <div className="min-w-0 space-y-1.5">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Pill variant="review" size="sm" icon={null}>
            {tf(`approvalKinds.${item.kind}`)}
          </Pill>
          <Link
            href={`/fees/accounts/${item.account_id}`}
            className="font-semibold text-primary underline underline-offset-4"
          >
            {item.student_name ?? t("student")}
          </Link>
          {item.class_section ? (
            <span className="text-sm text-ink-muted">{item.class_section}</span>
          ) : null}
        </p>
        <p className="break-words">{item.reason}</p>
        <p className="text-sm text-ink-muted">
          {t("requestedBy", {
            name: item.requested_by_name ?? "—",
            when: formatDateTime(item.requested_at) ?? "",
          })}
        </p>
        {item.mine ? <p className="text-sm">{t("mine")}</p> : null}
      </div>
      <div className="flex flex-col gap-3 sm:items-end">
        {item.amount || (item.receipt_number && item.receipt_id) ? (
          <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1 sm:justify-end">
            {item.amount ? (
              <Amount value={item.amount} className="num font-semibold whitespace-nowrap" />
            ) : null}
            {item.receipt_number && item.receipt_id ? (
              <Link
                href={`/fees/receipts/${item.receipt_id}`}
                className="num font-mono text-sm whitespace-nowrap text-primary underline underline-offset-4"
              >
                {item.receipt_number}
              </Link>
            ) : null}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2 sm:justify-end">
          {item.can_decide ? (
            <>
              <ActionDialog
                triggerLabel={t("approve")}
                triggerVariant="primary"
                triggerSize="sm"
                triggerDescription={who}
                title={t("approveTitle")}
                description={t("approveBody")}
                confirmLabel={t("approve")}
                consequence={t(`consequence.${item.kind}`)}
                stepUp
                schema={approveSchema}
                invalidate={invalidate}
                errorNamespace="fees"
                submit={(input) => decide("approve", { note: input.note })}
              >
                {(errors) => (
                  <TextAreaField
                    name="note"
                    label={t("note")}
                    error={errors.note}
                    maxLength={REASON_MAX}
                    rows={2}
                  />
                )}
              </ActionDialog>
              <ActionDialog
                triggerLabel={t("reject")}
                triggerVariant="secondary"
                triggerSize="sm"
                triggerDescription={who}
                title={t("rejectTitle")}
                confirmLabel={t("reject")}
                confirmVariant="danger"
                consequence={t("rejectConsequence")}
                stepUp
                schema={rejectSchema}
                invalidate={invalidate}
                errorNamespace="fees"
                submit={(input) => decide("reject", input)}
              >
                {(errors) => (
                  <TextAreaField
                    name="reason"
                    label={t("rejectReason")}
                    error={errors.reason}
                    maxLength={REASON_MAX}
                    rows={3}
                  />
                )}
              </ActionDialog>
            </>
          ) : null}
          {item.mine ? (
            <ActionDialog
              triggerLabel={t("withdraw")}
              triggerSize="sm"
              triggerDescription={who}
              title={t("withdrawTitle")}
              confirmLabel={t("withdraw")}
              consequence={t("withdrawConsequence")}
              schema={z.object({})}
              invalidate={invalidate}
              errorNamespace="fees"
              submit={() => decide("withdraw", undefined)}
            />
          ) : null}
        </div>
      </div>
    </li>
  );
}

function Inbox({
  title,
  description,
  items,
}: {
  title: string;
  description?: string;
  items: readonly ApprovalItem[];
}) {
  const t = useTranslations("fees.approvals");
  return (
    <Card
      title={title}
      {...(description ? { description } : {})}
      actions={
        <Badge tone="neutral">
          <span className="num">{t("count", { count: items.length })}</span>
        </Badge>
      }
    >
      <ul className="divide-y divide-border">
        {items.map((item) => (
          <Item key={`${item.kind}-${item.id}`} item={item} />
        ))}
      </ul>
    </Card>
  );
}

/**
 * The approvals inbox (FR-FEE-007/008/012/013; D17): concessions, adjustments, cancellations
 * and refunds waiting for a second person. Approving needs a recent MFA sign-in; nobody decides
 * their own request (the API refuses with 403 self_approval_forbidden).
 */
export function ApprovalsScreen() {
  const t = useTranslations("fees.approvals");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can(FEE_PERMISSIONS.filter((p) => p !== "fee.report"));
  const api = useBffClient("staff");
  const list = useApiQuery(KEYS.approvals, () => unwrap(api.GET("/api/v1/fees/approvals")), {
    enabled: me !== undefined && allowed,
  });
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
    />
  );
  if (me !== undefined && !allowed) {
    return (
      <div className="space-y-5">
        {header}
        <NoAccess />
      </div>
    );
  }
  return (
    <div className="space-y-5">
      {header}
      <FeesGate data={list}>
        {(items) => {
          if (items.length === 0) {
            return (
              <Card title={t("listTitle")}>
                <EmptyState icon="inbox" title={t("emptyTitle")} body={t("emptyBody")} />
              </Card>
            );
          }
          const waiting = items.filter((item) => !item.mine);
          const mine = items.filter((item) => item.mine);
          return (
            <>
              {waiting.length > 0 ? (
                <Inbox title={t("listTitle")} description={t("listBody")} items={waiting} />
              ) : null}
              {mine.length > 0 ? (
                <Inbox title={t("mineTitle")} description={t("mineBody")} items={mine} />
              ) : null}
            </>
          );
        }}
      </FeesGate>
    </div>
  );
}
