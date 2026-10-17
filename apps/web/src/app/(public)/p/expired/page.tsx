import { notFound } from "next/navigation";

/**
 * Where every dead link lands (`/p/{token}` after a failed exchange, and any path under /p the
 * group does not serve): the uniform expired page with status 404 (ADR-0046 decision 7).
 */
export default function ExpiredPage(): never {
  notFound();
}
