import { FleetScreen } from "@/features/platform/OperationsViews";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.fleet.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const first = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;

/** FR-PLT-023..025: GET /platform/deployments and /platform/fleet/versions. */
export default async function PlatformFleetPage({ searchParams }: Props) {
  const { status } = await searchParams;
  return <FleetScreen status={first(status) ?? ""} />;
}
