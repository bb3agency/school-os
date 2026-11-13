import { notFound } from "next/navigation";
import { PolicyDetailScreen } from "@/features/compliance/PolicyDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("compliancePolicies.title"));

type Props = { params: Promise<{ locale: string; policyId: string }> };

/** FC-10 (FR-COMP-066): one policy and who has and has not read it. */
export default async function CompliancePolicyPage({ params }: Props) {
  const { policyId } = await params;
  if (!UUID_PATTERN.test(policyId)) notFound();
  return <PolicyDetailScreen policyId={policyId} />;
}
