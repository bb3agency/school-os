import { SubscriptionsScreen } from "@/features/platform/BillingViews";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.subscriptions.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const first = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;

/** FR-PLT-012..014: GET /platform/subscriptions?status=; change plan, cancel. */
export default async function PlatformSubscriptionsPage({ searchParams }: Props) {
  const { status } = await searchParams;
  return <SubscriptionsScreen status={first(status) ?? ""} />;
}
