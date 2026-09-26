import { SupportView } from "@/features/platform/OperationsViews";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.support.title"));

export default function PlatformSupportPage() {
  return <SupportView tickets={ready([])} />;
}
