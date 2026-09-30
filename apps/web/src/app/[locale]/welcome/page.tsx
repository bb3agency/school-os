import { DedicatedWelcome } from "@/features/marketing/DedicatedWelcome";
import { HomeView } from "@/features/marketing/HomeView";
import { marketingEnabled, readMarketingSettings } from "@/features/marketing/settings";
import { pageMetadata } from "@/lib/metadata";

// Title only: robots stay noindex/nofollow from the locale layout, like every other page.
export const generateMetadata = pageMetadata((t) =>
  marketingEnabled() ? t("marketing.home.title") : t("marketing.dedicated.title"),
);

/**
 * Public home page (no session; FR-IAM-001). Signed-out visitors to the school home land here
 * (requireStaff in src/server/session/rsc.ts); deep links still go straight to sign-in. On a
 * dedicated host it is only a branded sign-in page (docs/17 §5.6).
 */
export default function WelcomePage() {
  if (!marketingEnabled()) return <DedicatedWelcome />;
  return <HomeView settings={readMarketingSettings()} />;
}
