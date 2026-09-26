import { FleetView } from "@/features/platform/OperationsViews";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.fleet.title"));

export default function PlatformFleetPage() {
  return <FleetView deployments={ready([])} versions={ready([])} />;
}
