import { UsersView } from "@/features/school/UsersView";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.users.title"));

/** FR-IAM-010..014: data from GET /users once the BFF is wired. */
export default function SchoolUsersPage() {
  return <UsersView users={ready([])} />;
}
