/** School detail tabs (docs/16 §5.3). Shared by the server page and the client screen. */
export const SCHOOL_TABS = [
  "overview",
  "subscription",
  "invoices",
  "usage",
  "deployment",
  "flags",
  "tickets",
] as const;
export type SchoolTab = (typeof SCHOOL_TABS)[number];

export function parseSchoolTab(value: string | string[] | undefined): SchoolTab {
  const candidate = Array.isArray(value) ? value[0] : value;
  return (SCHOOL_TABS as readonly string[]).includes(candidate ?? "")
    ? (candidate as SchoolTab)
    : "overview";
}
