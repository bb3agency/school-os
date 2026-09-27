import { InviteUserScreen } from "@/features/users/InviteUserScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.users.inviteForm.title"));

/** US-102 AC1, FR-IAM-010..012: invite a staff member with roles and classes (POST /users). */
export default function InviteUserPage() {
  return <InviteUserScreen />;
}
