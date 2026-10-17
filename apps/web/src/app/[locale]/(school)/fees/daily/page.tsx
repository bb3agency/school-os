import { DailyScreen } from "@/features/fees/ReportsScreens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.daily.title"));

/** FR-FEE-018/019: daily collection report and day close. */
export default function FeeDailyPage() {
  return <DailyScreen />;
}
