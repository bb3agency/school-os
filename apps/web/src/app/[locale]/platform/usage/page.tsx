import { UsageView } from "@/features/platform/OperationsViews";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.usage.title"));

export default function PlatformUsagePage() {
  return <UsageView usage={ready([])} />;
}
