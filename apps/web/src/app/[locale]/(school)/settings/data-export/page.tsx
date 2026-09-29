import { DataExportScreen } from "@/features/admin/DataExportScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("admin.dataExport.title"));

/**
 * US-1201 AC1, FR-ADM-001: the school's full data export. POST /admin/tenant-export and the
 * download link need `tenant.export_all` (the owner) and a fresh MFA sign-in; open while the
 * school is suspended (docs/16 §5.5).
 */
export default function DataExportPage() {
  return <DataExportScreen />;
}
