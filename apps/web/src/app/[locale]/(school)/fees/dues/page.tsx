import { DuesScreen } from "@/features/fees/ReportsScreens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.dues.title"));

/** FR-FEE-020: students with overdue amounts. */
export default function FeeDuesListPage() {
  return <DuesScreen />;
}
