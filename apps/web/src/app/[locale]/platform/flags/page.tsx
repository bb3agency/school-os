import { FlagsScreen } from "@/features/platform/OperationsViews";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.flags.title"));

/** FR-PLT-022: GET/PUT /platform/flags. */
export default function PlatformFlagsPage() {
  return <FlagsScreen />;
}
