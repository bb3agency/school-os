import { OperatorsView } from "@/features/platform/OperatorsView";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.operators.title"));

export default function PlatformOperatorsPage() {
  return <OperatorsView operators={ready([])} />;
}
