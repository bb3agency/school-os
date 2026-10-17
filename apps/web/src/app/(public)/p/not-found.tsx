import { ExpiredLink } from "@/features/public/PublicPages";

/** The uniform expired page, status 404 (ADR-0046 decision 7). */
export default function PublicNotFound() {
  return <ExpiredLink />;
}
