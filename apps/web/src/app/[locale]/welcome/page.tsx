import { WelcomeView } from "@/features/welcome/WelcomeView";
import { pageMetadata } from "@/lib/metadata";

// Title only: robots stay noindex/nofollow from the locale layout, like every other page.
export const generateMetadata = pageMetadata((t) => t("welcome.title"));

/**
 * Public product page (no session). Signed-out visitors to the school home land here
 * (requireStaff in src/server/session/rsc.ts); deep links still go straight to sign-in.
 */
export default function WelcomePage() {
  return <WelcomeView />;
}
