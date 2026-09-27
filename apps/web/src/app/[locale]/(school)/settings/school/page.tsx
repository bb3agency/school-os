import { SchoolSettingsScreen } from "@/features/settings/SchoolSettingsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("schoolSettings.title"));

/** FR-TEN-012: GET /tenant (any member), PATCH /tenant (tenant.settings.manage, step-up). */
export default function SchoolSettingsPage() {
  return <SchoolSettingsScreen />;
}
