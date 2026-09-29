import { NoticesScreen } from "@/features/circulars/NoticesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("notices.title"));

/** US-1605: parent notices (drafts and approved) and starting a new one (`notice.draft`). */
export default function NoticesPage() {
  return <NoticesScreen />;
}
