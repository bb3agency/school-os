import { FlagsView } from "@/features/platform/OperationsViews";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.flags.title"));

export default function PlatformFlagsPage() {
  return <FlagsView flags={ready([])} />;
}
