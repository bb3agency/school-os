import type { SchoolClass } from "@schoolos/api-client";

/**
 * Class name in the UI language (the API stores English and Telugu names; NFR-I18N-001).
 * Shared by the academic-structure screen, the student list and anything else that shows a
 * class. A plain function, safe on the server and in the browser.
 */
export function classLabel(
  schoolClass: Pick<SchoolClass, "display_en" | "display_te">,
  locale: string,
): string {
  return locale === "te" && schoolClass.display_te
    ? schoolClass.display_te
    : schoolClass.display_en;
}
