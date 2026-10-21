import { NewBreachScreen } from "@/features/breaches/NewBreachScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("breaches.form.title"));

/** ADR-0050, US-2701: record a breach (owner, principal; step-up). */
export default function NewBreachPage() {
  return <NewBreachScreen />;
}
