import { headers } from "next/headers";
import { notFound } from "next/navigation";
import { BusyLink, LinkFrame } from "@/features/public/PublicPages";
import { getAuthRuntime } from "@/server/runtime";
import { loadLinkView } from "@/server/public/view";

/**
 * `/p/view`: the parent page for the link in the parent cookie (ADR-0046 decision 6,
 * FR-PUB-008, FR-PUB-010). The API re-resolves the link on every request and counts the view;
 * a dead link is the uniform expired page with status 404.
 */
export const dynamic = "force-dynamic";

export default async function ParentLinkPage() {
  const runtime = await getAuthRuntime();
  const view = await loadLinkView(runtime, new Headers(await headers()));
  if (view.state === "expired") notFound();
  if (view.state === "busy") return <BusyLink />;
  return <LinkFrame envelope={view.envelope} />;
}
