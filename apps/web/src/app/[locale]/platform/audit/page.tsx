import { PlatformAuditView } from "@/features/platform/OperationsViews";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.audit.title"));

export default function PlatformAuditPage() {
  return <PlatformAuditView events={ready([])} />;
}
