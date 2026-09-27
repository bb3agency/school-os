import { notFound } from "next/navigation";
import { UserDetailScreen } from "@/features/users/UserDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("school.users.detail.title"));

type Props = { params: Promise<{ locale: string; userId: string }> };

/** US-102, FR-IAM-012..014: one staff member's status, roles and classes (the URL holds only the ID). */
export default async function UserPage({ params }: Props) {
  const { userId } = await params;
  if (!UUID_PATTERN.test(userId)) notFound();
  return <UserDetailScreen userId={userId} />;
}
