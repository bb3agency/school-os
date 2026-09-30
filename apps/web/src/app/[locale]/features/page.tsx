import { notFound } from "next/navigation";
import { FeaturesView } from "@/features/marketing/FeaturesView";
import { marketingEnabled, readMarketingSettings } from "@/features/marketing/settings";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("marketing.features.title"));

/** Public features page (shared SaaS only; 404 on a dedicated host). */
export default function FeaturesPage() {
  if (!marketingEnabled()) notFound();
  return <FeaturesView settings={readMarketingSettings()} />;
}
