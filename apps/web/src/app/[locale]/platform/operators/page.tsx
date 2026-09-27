import { OperatorsScreen } from "@/features/platform/OperatorsView";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.operators.title"));

/** FR-PLT-028: GET/POST /platform/operators. */
export default function PlatformOperatorsPage() {
  return <OperatorsScreen />;
}
