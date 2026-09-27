import { UsersScreen } from "@/features/users/UsersScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.users.title"));

/** US-102, FR-IAM-010..014: the school's staff, their roles and classes (GET /users, /roles). */
export default function SchoolUsersPage() {
  return <UsersScreen />;
}
