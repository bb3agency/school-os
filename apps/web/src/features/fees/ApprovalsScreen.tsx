"use client";

import { useTranslations } from "next-intl";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
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
  return (
    <li className="space-y-2 border-b border-border pb-4 last:border-0">
      <p className="font-semibold">
        {tf(`approvalKinds.${item.kind}`)} ·{" "}
        <Link
          href={`/fees/accounts/${item.account_id}`}
          className="text-primary underline underline-offset-4"
        >
          {item.student_name ?? t("student")}
        </Link>{" "}
        <span className="font-normal text-ink-muted">{item.class_section}</span>
        {item.amount ? (
          <>
            {" "}
            · <Amount value={item.amount} />
          </>
        ) : null}
        {item.receipt_number && item.receipt_id ? (
          <>
            {" "}
            ·{" "}
            <Link
              href={`/fees/receipts/${item.receipt_id}`}
              className="text-primary underline underline-offset-4"
            >
              {item.receipt_number}
            </Link>
          </>
        ) : null}
      </p>
      <p>{item.reason}</p>
      <p className="text-sm text-ink-muted">
        {t("requestedBy", {
          name: item.requested_by_name ?? "—",
          when: formatDateTime(item.requested_at) ?? "",
        })}
      </p>
      <div className="flex flex-wrap gap-2">
        {item.can_decide ? (
          <>
            <ActionDialog
              triggerLabel={t("approve")}
              triggerVariant="primary"
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
              triggerVariant="danger"
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
          <>
            <p className="text-sm">{t("mine")}</p>
            <ActionDialog
              triggerLabel={t("withdraw")}
              title={t("withdrawTitle")}
              confirmLabel={t("withdraw")}
              consequence={t("withdrawConsequence")}
              schema={z.object({})}
              invalidate={invalidate}
              errorNamespace="fees"
              submit={() => decide("withdraw", undefined)}
            />
          </>
        ) : null}
      </div>
    </li>
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
      <div className="space-y-6">
        {header}
        <NoAccess />
      </div>
    );
  }
  return (
    <div className="space-y-6">
      {header}
      <FeesGate data={list}>
        {(items) => (
          <Card title={t("listTitle")}>
            {items.length === 0 ? (
              <EmptyState icon="inbox" title={t("emptyTitle")} body={t("emptyBody")} />
            ) : (
              <ul className="space-y-4">
                {items.map((item) => (
                  <Item key={`${item.kind}-${item.id}`} item={item} />
                ))}
              </ul>
            )}
          </Card>
        )}
      </FeesGate>
    </div>
  );
}
