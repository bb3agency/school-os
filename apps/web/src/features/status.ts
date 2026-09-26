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
  healthy: "success",
  degraded: "warning",
  down: "danger",
  provisioning: "info",
  unknown: "neutral",
};

export const schoolTone: Record<TenantStatus, BadgeTone> = {
  provisioning: "info",
  active: "success",
  suspended: "danger",
  offboarding: "warning",
};

export const ticketTone: Record<TicketStatus, BadgeTone> = {
  open: "info",
  pending: "warning",
  resolved: "success",
  closed: "neutral",
};

export const priorityTone: Record<TicketPriority, BadgeTone> = {
  low: "neutral",
  normal: "neutral",
  high: "warning",
  urgent: "danger",
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
  active: "success",
  retired: "neutral",
};

export const announcementTone: Record<AnnouncementStatus, BadgeTone> = {
  draft: "neutral",
  scheduled: "info",
  live: "success",
  ended: "neutral",
};

export const breakGlassTone: Record<BreakGlassStatus, BadgeTone> = {
  pending: "warning",
  approved: "info",
  active: "danger",
  expired: "neutral",
  rejected: "neutral",
  revoked: "neutral",
};
