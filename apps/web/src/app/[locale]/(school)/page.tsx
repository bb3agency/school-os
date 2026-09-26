import { HomeView } from "@/features/school/HomeView";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.home.title"));

/** School home. Summary counts come from the BFF in a later task. */
export default function SchoolHomePage() {
  return <HomeView summary={null} />;
}
