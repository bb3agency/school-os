import { PlansView } from "@/features/platform/BillingViews";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.plans.title"));

export default function PlatformPlansPage() {
  return <PlansView plans={ready([])} />;
}
