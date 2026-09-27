import { RegisterPhotosScreen } from "@/features/extraction/RegisterPhotosScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("extraction.list.title"));

/** US-402 / FR-IMP-020..023: register-page photos and their verification queue (import.run). */
export default function RegisterPhotosPage() {
  return <RegisterPhotosScreen />;
}
