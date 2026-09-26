import { SubscriptionsView } from "@/features/platform/BillingViews";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.subscriptions.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function PlatformSubscriptionsPage({ searchParams }: Props) {
  const { status } = await searchParams;
  return (
    <SubscriptionsView
      subscriptions={ready([])}
      status={typeof status === "string" ? status : ""}
    />
  );
}
