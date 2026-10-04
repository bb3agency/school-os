import type {
  AnnouncementStatus,
  BreakGlassStatus,
  DeploymentStatus,
  InvoiceStatus,
  MemberStatus,
  PersonStatus,
  PlanStatus,
  SubscriptionStatus,
  TenantStatus,
  TicketPriority,
  TicketStatus,
} from "@schoolos/api-client";
import type { BadgeTone } from "@/components/ui/Badge";

/** Badge colour per status. The badge text (translated) always names the status too. */
export const subscriptionTone: Record<SubscriptionStatus, BadgeTone> = {
  trial: "info",
  active: "success",
  past_due: "warning",
  suspended: "danger",
  cancelled: "neutral",
};

export const invoiceTone: Record<InvoiceStatus, BadgeTone> = {
  draft: "neutral",
  issued: "info",
  paid: "success",
  void: "neutral",
};

export const deploymentTone: Record<DeploymentStatus, BadgeTone> = {
  provisioning: "info",
  healthy: "success",
  degraded: "warning",
  unreachable: "danger",
  decommissioned: "neutral",
};

export const schoolTone: Record<TenantStatus, BadgeTone> = {
  provisioning: "info",
  active: "success",
  suspended: "danger",
  offboarding: "warning",
  deleted: "neutral",
};

export const ticketTone: Record<TicketStatus, BadgeTone> = {
  open: "info",
  in_progress: "info",
  waiting_on_school: "warning",
  resolved: "success",
  closed: "neutral",
};

export const priorityTone: Record<TicketPriority, BadgeTone> = {
  p1: "danger",
  p2: "warning",
  p3: "neutral",
  p4: "neutral",
};

export const personTone: Record<PersonStatus, BadgeTone> = {
  invited: "info",
  active: "success",
  deactivated: "neutral",
};

/** School staff membership status (tenant API UserOut.status). */
export const memberTone: Record<MemberStatus, BadgeTone> = {
  invited: "info",
  active: "success",
  suspended: "warning",
  removed: "neutral",
};

export const planTone: Record<PlanStatus, BadgeTone> = {
  draft: "neutral",
  published: "success",
  retired: "neutral",
};

/** Stored status, plus "ended" (shown once the end time has passed; see AnnouncementsView). */
export const announcementTone: Record<AnnouncementStatus | "ended", BadgeTone> = {
  draft: "neutral",
  scheduled: "info",
  cancelled: "neutral",
  ended: "neutral",
};

export const breakGlassTone: Record<BreakGlassStatus, BadgeTone> = {
  requested: "warning",
  approved: "info",
  active: "danger",
  expired: "neutral",
  revoked: "neutral",
  denied: "neutral",
};

/**
 * For statuses the API documents as plain strings: the known value, or null so the UI
 * shows the raw code in a neutral badge instead of crashing on something new.
 */
export function known<T extends string>(table: Record<T, BadgeTone>, value: string): T | null {
  return Object.prototype.hasOwnProperty.call(table, value) ? (value as T) : null;
}
