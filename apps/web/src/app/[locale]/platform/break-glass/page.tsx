import { BreakGlassScreen } from "@/features/platform/OperationsViews";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.breakGlass.title"));

/** docs/16 §5.15: GET/POST /platform/break-glass-requests. */
export default function PlatformBreakGlassPage() {
  return <BreakGlassScreen />;
}
