import { headers } from "next/headers";
import { notFound } from "next/navigation";
import { BusyLink, LinkFrame, type PageNotice } from "@/features/public/PublicPages";
import { PAY_ERRORS, type PayError } from "@/server/public/actions";
import { getAuthRuntime } from "@/server/runtime";
import { loadLinkView } from "@/server/public/view";

/**
 * `/p/view`: the parent page for the link in the parent cookie (ADR-0046 decisions 2 and 6,
 * FR-PUB-008..010): notice, receipt, dues or opt-in. The API re-resolves the link on every
 * request and counts the view; a dead link is the uniform expired page with status 404. The
 * query carries only reason codes from the page's own forms (`pay`, `pdf`, `saved`).
 */
export const dynamic = "force-dynamic";

function notice(query: Record<string, string | string[] | undefined>): PageNotice | null {
  const pay = query.pay;
  if (typeof pay === "string" && (PAY_ERRORS as readonly string[]).includes(pay)) {
    return { kind: "pay", error: pay as PayError };
  }
  if (query.pdf === "0") return { kind: "pdf" };
  if (query.saved === "1") return { kind: "saved", ok: true };
  if (query.saved === "0") return { kind: "saved", ok: false };
  return null;
}

export default async function ParentLinkPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const runtime = await getAuthRuntime();
  const view = await loadLinkView(runtime, new Headers(await headers()));
  if (view.state === "expired") notFound();
  if (view.state === "busy") return <BusyLink />;
  return (
    <LinkFrame envelope={view.envelope} csrf={view.csrf} notice={notice(await searchParams)} />
  );
}
