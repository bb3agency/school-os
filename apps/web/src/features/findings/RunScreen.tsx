"use client";

import { useTranslations } from "next-intl";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { Link } from "@/i18n/navigation";
import { RunProgress } from "./RunChecks";

/** One check run (the `dq.run.completed` notification links here). */
export function RunScreen({ runId }: { runId: string }) {
  const t = useTranslations("findings.run");
  return (
    <div className="space-y-6">
      <PageHeader title={t("pageTitle")} description={t("pageDescription")} />
      <Card>
        <RunProgress runId={runId} />
      </Card>
      <p>
        <Link href="/findings" className="text-primary underline">
          {t("seeFindings")}
        </Link>
      </p>
    </div>
  );
}
