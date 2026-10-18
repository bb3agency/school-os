import { headers } from "next/headers";
import { BusyLink, PaidPage } from "@/features/public/PublicPages";
import { loadPayment } from "@/server/public/actions";
import { loadLinkView } from "@/server/public/view";
import { getAuthRuntime } from "@/server/runtime";
import { notFound } from "next/navigation";

/**
 * `/p/paid`: where Razorpay sends the parent back (the payment link's callback; R2-3H, US-2403
 * AC1). Reads the status through the cookie's dues link (never Razorpay's query values, which
 * anyone can type) and checks again every 5 seconds for a minute without client JavaScript.
 */
export const dynamic = "force-dynamic";

const MAX_CHECKS = 12;

function one(value: string | string[] | undefined): string | null {
  return typeof value === "string" ? value : null;
}

export default async function ParentPaidPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const ref = one(query.ref) ?? one(query.razorpay_payment_link_reference_id);
  const checks = Math.min(Math.max(Number(one(query.n) ?? "0") || 0, 0), MAX_CHECKS);
  const runtime = await getAuthRuntime();
  const incoming = new Headers(await headers());
  const view = await loadLinkView(runtime, incoming);
  if (view.state === "expired" || (view.state === "ok" && view.envelope.kind !== "parent_dues")) {
    notFound();
  }
  if (view.state === "busy") return <BusyLink />;
  const payment = await loadPayment(
    runtime,
    incoming,
    ref && /^[0-9a-f]{32}$/.test(ref) ? ref : null,
  );
  if (payment.state === "busy") return <BusyLink />;
  return (
    <PaidPage
      envelope={view.envelope}
      payment={payment.state === "ok" ? payment.payment : null}
      refValue={ref && /^[0-9a-f]{32}$/.test(ref) ? ref : null}
      checks={checks}
      maxChecks={MAX_CHECKS}
    />
  );
}
