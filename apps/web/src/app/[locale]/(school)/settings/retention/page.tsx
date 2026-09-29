import { RetentionScreen } from "@/features/admin/RetentionScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("admin.retention.title"));

/** US-1201, FR-ADM-002: GET/PUT /admin/retention (`tenant.settings.manage`, step-up to save). */
export default function RetentionPage() {
  return <RetentionScreen />;
}
