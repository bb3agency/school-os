import { notFound } from "next/navigation";
import { PricingView } from "@/features/marketing/PricingView";
import { marketingEnabled, readMarketingSettings } from "@/features/marketing/settings";
import { pageMetadata } from "@/lib/metadata";
import { ClientMessages } from "@/i18n/ClientMessages";

export const generateMetadata = pageMetadata((t) => t("marketing.pricing.title"));

/** Public pricing page, plans described without prices (shared SaaS only; 404 on dedicated). */
export default function PricingPage() {
  if (!marketingEnabled()) notFound();
  return (
    <ClientMessages scope="marketing">
      <PricingView settings={readMarketingSettings()} />
    </ClientMessages>
  );
}
