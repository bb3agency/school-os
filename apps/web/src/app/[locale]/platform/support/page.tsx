import { PlatformSupportScreen } from "@/features/platform/SupportScreens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.support.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const first = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;

/** FR-PLT-027: the operator ticket queue (GET /platform/support/tickets). */
export default async function PlatformSupportPage({ searchParams }: Props) {
  const params = await searchParams;
  return (
    <PlatformSupportScreen
      filters={{
        ...(first(params.status) ? { status: first(params.status) as string } : {}),
        ...(first(params.priority) ? { priority: first(params.priority) as string } : {}),
        mine: first(params.mine) === "true",
      }}
    />
  );
}
