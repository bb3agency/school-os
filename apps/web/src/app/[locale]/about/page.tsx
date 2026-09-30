import { notFound } from "next/navigation";
import { AboutView } from "@/features/marketing/AboutView";
import { marketingEnabled, readMarketingSettings } from "@/features/marketing/settings";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("marketing.about.title"));

/** Public about and contact page (shared SaaS only; 404 on a dedicated host). */
export default function AboutPage() {
  if (!marketingEnabled()) notFound();
  return <AboutView settings={readMarketingSettings()} />;
}
