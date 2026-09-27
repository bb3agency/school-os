import { BreakGlassScreen } from "@/features/break-glass/BreakGlassScreens";
import { grantStatusFilter } from "@/features/break-glass/filters";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("breakGlass.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

/** US-103, FR-OPS-004, SEC-021: support-access requests and grants of this school. */
export default async function BreakGlassPage({ searchParams }: Props) {
  const params = await searchParams;
  return <BreakGlassScreen status={grantStatusFilter(params.status)} />;
}
