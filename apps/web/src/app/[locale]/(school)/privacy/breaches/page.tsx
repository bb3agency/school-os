import { BreachesScreen } from "@/features/breaches/BreachesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("breaches.title"));

/** ADR-0050, US-2701: the school's breach register with each breach's next duty. */
export default function BreachesPage() {
  return <BreachesScreen />;
}
