import { notFound } from "next/navigation";
import { AccountScreen } from "@/features/fees/AccountScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("fees.account.title"));

type Props = { params: Promise<{ locale: string; accountId: string }> };

/** FR-FEE-021: a student's fee account, statement and requests. */
export default async function FeeAccountPage({ params }: Props) {
  const { accountId } = await params;
  if (!UUID_PATTERN.test(accountId)) notFound();
  return <AccountScreen accountId={accountId} />;
}
