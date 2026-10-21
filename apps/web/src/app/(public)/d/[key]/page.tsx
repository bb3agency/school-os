import { headers } from "next/headers";
import { notFound } from "next/navigation";
import { DisclosureBusy, DisclosurePage } from "@/features/public/DisclosurePages";
import { getAuthRuntime } from "@/server/runtime";
import { DISCLOSURE_KEY, loadDisclosure } from "@/server/public/disclosure";

/**
 * `/d/{key}` on PUBLIC_BASE_URL: a school's published CBSE Appendix IX disclosure (ADR-0051;
 * FR-DISC-009, FR-DISC-010). An unknown, changed, withdrawn or unpublished link, or a school that
 * is not active, is the uniform "not found" page with status 404.
 */
export const dynamic = "force-dynamic";

export default async function DisclosureKeyPage({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  if (!DISCLOSURE_KEY.test(key)) notFound();
  const runtime = await getAuthRuntime();
  const view = await loadDisclosure(runtime, new Headers(await headers()), key);
  if (view.state === "not_found") notFound();
  if (view.state === "busy") return <DisclosureBusy />;
  return <DisclosurePage disclosure={view.disclosure} docBase={`/d/${view.key}`} />;
}
