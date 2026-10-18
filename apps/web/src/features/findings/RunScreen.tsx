"use client";

import { useTranslations } from "next-intl";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { RunProgress } from "./RunChecks";

/**
 * One check run (the `dq.run.completed` notification links here): its status while it is
 * queued or running (polled, slowing down to every 15 s) and the result counts once done.
 */
export function RunScreen({ runId }: { runId: string }) {
  const t = useTranslations("findings.run");
  const tf = useTranslations("findings");
  return (
    <div className="space-y-5">
      <PageHeader
        breadcrumb={[{ label: tf("title"), href: "/findings" }, { label: t("pageTitle") }]}
        title={t("pageTitle")}
        description={t("pageDescription")}
        actions={<ButtonLink href="/findings">{t("seeFindings")}</ButtonLink>}
      />
      <Card>
        <RunProgress runId={runId} detailed />
      </Card>
    </div>
  );
}
