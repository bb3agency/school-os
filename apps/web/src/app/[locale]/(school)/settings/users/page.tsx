import { UsersScreen } from "@/features/school/screens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.users.title"));

/** FR-IAM-010..014: GET /users through the BFF (step-up handled by the client). */
export default function SchoolUsersPage() {
  return <UsersScreen />;
}
