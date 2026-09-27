import { PlansScreen } from "@/features/platform/BillingViews";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.plans.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const first = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;

/** FR-PLT-010..011: GET/POST /platform/plans; publish and retire. */
export default async function PlatformPlansPage({ searchParams }: Props) {
  const { status } = await searchParams;
  return <PlansScreen status={first(status) ?? ""} />;
}
