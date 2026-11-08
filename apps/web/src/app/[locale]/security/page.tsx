import { notFound } from "next/navigation";
import { SecurityView } from "@/features/marketing/SecurityView";
import { marketingEnabled, readMarketingSettings } from "@/features/marketing/settings";
import { pageMetadata } from "@/lib/metadata";
import { ClientMessages } from "@/i18n/ClientMessages";

export const generateMetadata = pageMetadata((t) => t("marketing.security.title"));

/** Public security and privacy page (shared SaaS only; 404 on a dedicated host). */
export default function SecurityPage() {
  if (!marketingEnabled()) notFound();
  return (
    <ClientMessages scope="marketing">
      <SecurityView settings={readMarketingSettings()} />
    </ClientMessages>
  );
}
