import { notFound } from "next/navigation";
import { FeaturesView } from "@/features/marketing/FeaturesView";
import { marketingEnabled, readMarketingSettings } from "@/features/marketing/settings";
import { pageMetadata } from "@/lib/metadata";
import { ClientMessages } from "@/i18n/ClientMessages";

export const generateMetadata = pageMetadata((t) => t("marketing.features.title"));

/** Public features page (shared SaaS only; 404 on a dedicated host). */
export default function FeaturesPage() {
  if (!marketingEnabled()) notFound();
  return (
    <ClientMessages scope="marketing">
      <FeaturesView settings={readMarketingSettings()} />
    </ClientMessages>
  );
}
