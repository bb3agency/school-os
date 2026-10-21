import { DisclosureNotFound } from "@/features/public/DisclosurePages";

/** The one "not found" for every disclosure link that does not open a page (ADR-0051). */
export default function DisclosureKeyNotFound() {
  return <DisclosureNotFound />;
}
