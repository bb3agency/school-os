import { notFound } from "next/navigation";
import { RunScreen } from "@/features/findings/RunScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("findings.run.pageTitle"));

type Props = { params: Promise<{ locale: string; runId: string }> };

/** FR-DQ-002: one check run (the `dq.run.completed` notification links here). */
export default async function FindingRunPage({ params }: Props) {
  const { runId } = await params;
  if (!UUID_PATTERN.test(runId)) notFound();
  return <RunScreen runId={runId} />;
}
