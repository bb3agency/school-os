import { BreakGlassView } from "@/features/platform/OperationsViews";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.breakGlass.title"));

export default function PlatformBreakGlassPage() {
  return <BreakGlassView requests={ready([])} />;
}
