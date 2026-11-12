"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { toLoadable, useBffClient } from "@/lib/bff/query";
import { formatDate } from "@/lib/format";
import {
  SAFETY_KEYS,
  acknowledgePolicy,
  fetchMyPolicies,
  openMyPolicy,
  type MyPolicy,
} from "./safety-data";

/**
 * "Policies to read" (FC-10; FR-COMP-063..FR-COMP-067): every school policy the signed-in
 * member is asked to read. They open the exact version (a short-lived link) and then confirm
 * "I have read this version"; the confirm button appears only after opening, and names the
 * version they opened. Any signed-in member (no permission needed).
 */
export function MyPoliciesScreen() {
  const t = useTranslations("myPolicies");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  // Policy id -> the version the member opened in this visit.
  const [opened, setOpened] = useState<Record<string, string>>({});
  const mine = useQuery({
    queryKey: SAFETY_KEYS.mine,
    queryFn: () => fetchMyPolicies(api),
    retry: false,
  });
  const open = useMutation({
    mutationFn: (policy: MyPolicy) => openMyPolicy(api, policy.id),
    onSuccess: (file, policy) => {
      setOpened((prev) => ({ ...prev, [policy.id]: file.document_version_id }));
      window.open(file.url, "_blank", "noopener,noreferrer");
    },
  });
  const confirm = useMutation({
    mutationFn: (policy: MyPolicy) => acknowledgePolicy(api, policy.id, opened[policy.id] ?? ""),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SAFETY_KEYS.mine }),
    onError: (_error, policy) =>
      setOpened((prev) => {
        const next = { ...prev };
        delete next[policy.id];
        return next;
      }),
  });

  const columns: ColumnV2<MyPolicy>[] = [
    {
      key: "policy",
      stack: "title",
      header: t("title"),
      cell: (p) => <span className="font-semibold break-words text-ink">{p.title}</span>,
    },
    {
      key: "status",
      header: t("toRead"),
      cell: (p) =>
        p.acknowledged_at ? (
          <span className="num text-sm whitespace-nowrap">
            {t("readOn", { date: formatDate(p.acknowledged_at) ?? "" })}
          </span>
        ) : p.current_version_id ? (
          <Pill variant="review">{t("toRead")}</Pill>
        ) : (
          <span className="text-sm text-ink-muted">{t("notReady")}</span>
        ),
    },
    {
      key: "actions",
      header: "",
      cell: (p) =>
        p.current_version_id ? (
          <span className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant={p.needs_ack && !opened[p.id] ? "primary" : "secondary"}
              onClick={() => open.mutate(p)}
              aria-label={t("openAria", { name: p.title })}
            >
              {t("open")}
            </Button>
            {p.needs_ack && opened[p.id] ? (
              <Button
                size="sm"
                variant="primary"
                onClick={() => confirm.mutate(p)}
                disabled={confirm.isPending}
                aria-label={t("confirmAria", { name: p.title })}
              >
                {t("confirm")}
              </Button>
            ) : null}
          </span>
        ) : null,
    },
  ];

  const anyOpened = Object.keys(opened).length > 0;
  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {mine.data ? (
        <p className="text-sm text-ink" role="status">
          {t("pending", { count: mine.data.pending })}
        </p>
      ) : null}
      {anyOpened && !confirm.isSuccess ? (
        <p className="text-sm text-ink-muted">{t("opened")}</p>
      ) : null}
      <ApiErrorAlert error={open.error ?? confirm.error} namespace="myPolicies" />
      <DataTableV2
        id="my-policies"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...mine, data: mine.data?.items })}
        rowKey={(p) => p.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
        onRetry={() => void mine.refetch()}
      />
    </div>
  );
}
