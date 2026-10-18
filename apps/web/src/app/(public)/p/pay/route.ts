import { payFromForm } from "@/server/public/actions";
import { getAuthRuntime } from "@/server/runtime";

/**
 * POST /p/pay: "Pay online" from a dues page (R2-3H; ADR-0046 decision 9; FR-PAY-005). A plain
 * form post (parent cookie, CSRF token, public origin); 303 to the school's Razorpay page or
 * back to /p/view with the reason.
 */
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  return payFromForm(await getAuthRuntime(), request);
}
