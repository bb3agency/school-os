import type { IconName } from "@/components/ui/Icon";
import type { NavItem, NavSection } from "@/components/ui/SidebarNav";

/**
 * Command palette quick actions and the phone bottom bars for the school console
 * (docs/17 §5.2, §5.9). Every href is an existing page; every entry carries the permission
 * (and per-school feature) of that page, filtered with the SAME rule as the sidebar
 * (SchoolShell `allowed`). UX only: the API checks every call.
 */
export interface SchoolAction {
  id: string;
  /** Key under `ui.palette`. */
  labelKey:
    | "addStudent"
    | "newChangeRequest"
    | "uploadDocument"
    | "collectFee"
    | "takeAttendance"
    | "enterMarks"
    | "askSchool"
    | "inviteUser"
    | "studentList";
  href: string;
  icon: IconName;
  permission: string | readonly string[];
  feature?: "fees" | "tally" | "messaging";
}

export const SCHOOL_ACTIONS: readonly SchoolAction[] = [
  {
    id: "add-student",
    labelKey: "addStudent",
    href: "/students/new",
    icon: "plus",
    permission: "student.create",
  },
  {
    id: "collect-fee",
    labelKey: "collectFee",
    href: "/fees/collect",
    icon: "receipt",
    permission: "fee.collect",
    feature: "fees",
  },
  {
    id: "take-attendance",
    labelKey: "takeAttendance",
    href: "/attendance",
    icon: "check",
    permission: "attendance.record",
  },
  {
    id: "enter-marks",
    labelKey: "enterMarks",
    href: "/marks",
    icon: "chart",
    permission: "marks.record",
  },
  {
    id: "new-change-request",
    labelKey: "newChangeRequest",
    href: "/change-requests/new",
    icon: "clipboard",
    permission: "student.identity_change.request",
  },
  {
    id: "upload-document",
    labelKey: "uploadDocument",
    href: "/documents/new",
    icon: "upload",
    permission: "document.upload",
  },
  { id: "ask", labelKey: "askSchool", href: "/ask", icon: "sparkles", permission: "kb.ask" },
  {
    id: "student-list",
    labelKey: "studentList",
    href: "/exports/new/student-list",
    icon: "file",
    permission: "student.export",
  },
  {
    id: "invite-user",
    labelKey: "inviteUser",
    href: "/settings/users/new",
    icon: "users",
    permission: "user.manage",
  },
];

/** Roles with a phone-first job and the destinations of their bottom bar (report §2). */
const BOTTOM_BARS: ReadonlyArray<{ roles: readonly string[]; hrefs: readonly string[] }> = [
  { roles: ["class_teacher", "teacher"], hrefs: ["/", "/attendance", "/marks", "/ask"] },
  { roles: ["principal", "owner"], hrefs: ["/", "/change-requests", "/ask", "/messages"] },
];

/**
 * The bottom bar for this person: the first matching role's destinations, keeping only the
 * pages their (already permission-filtered) sidebar shows. Fewer than two left: no bar.
 */
export function bottomBarFor(
  roles: readonly string[] | null | undefined,
  sections: readonly NavSection[],
): NavItem[] {
  const bar = BOTTOM_BARS.find((entry) => entry.roles.some((role) => roles?.includes(role)));
  if (!bar) return [];
  const items = sections.flatMap((section) => section.items);
  const picked = bar.hrefs
    .map((href) => items.find((item) => item.href === href))
    .filter((item): item is NavItem => item !== undefined)
    .map(({ href, label, icon, exact }) => ({
      href,
      label,
      ...(icon ? { icon } : {}),
      ...(exact ? { exact } : {}),
    }));
  return picked.length >= 2 ? picked : [];
}
