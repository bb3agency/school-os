import { RulesScreen } from "@/features/findings/RulesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("findings.rules.title"));

/** FR-DQ-001: the rule catalog and submission profiles, read-only. */
export default function FindingRulesPage() {
  return <RulesScreen />;
}
