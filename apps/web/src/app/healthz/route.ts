/** Liveness probe for the container HEALTHCHECK and load balancer. Public, no data. */
export const dynamic = "force-dynamic";

export function GET(): Response {
  return Response.json({ status: "ok" }, { headers: { "Cache-Control": "no-store" } });
}
