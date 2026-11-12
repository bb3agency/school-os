import { MyPoliciesScreen } from "@/features/compliance/MyPoliciesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("myPolicies.title"));

/** FC-10 (FR-COMP-063): the school policies the signed-in member is asked to read. */
export default function MyPoliciesPage() {
  return <MyPoliciesScreen />;
}
