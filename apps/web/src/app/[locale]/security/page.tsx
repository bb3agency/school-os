import { notFound } from "next/navigation";
import { SecurityView } from "@/features/marketing/SecurityView";
import { marketingEnabled, readMarketingSettings } from "@/features/marketing/settings";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("marketing.security.title"));

/** Public security and privacy page (shared SaaS only; 404 on a dedicated host). */
export default function SecurityPage() {
  if (!marketingEnabled()) notFound();
  return <SecurityView settings={readMarketingSettings()} />;
}
