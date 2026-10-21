"use client";

import type { components } from "@schoolos/api-client";
import * as z from "@/lib/zod";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * WhatsApp parent messages from the school's own number (R2-1C; ADR-0043). Every route answers
 * 404 `module_unavailable` on a dedicated host or while messaging is off (FR-MSG-024): the
 * screens then say so. Phones appear only as their last four digits (FR-MSG-017); nothing
 * personal goes into a URL (the delivery log searches with a POST body). The API checks every
 * permission; these values only decide what the screens offer.
 */

export const CONFIGURE = "messaging.configure";
export const RECORD = "messaging.optin.record";
export const READ = "message.read";
/** R2-3G: send an approved notice, send again, revoke a parent link (principal, office admin). */
export const SEND = "message.send";
export const MESSAGING_PERMISSIONS = [CONFIGURE, RECORD, READ, SEND] as const;
export const CATEGORIES = ["notices", "attendance", "fees"] as const;
export type Category = (typeof CATEGORIES)[number];

type S = components["schemas"];
export type Account = S["MessagingAccountOut"];
export type Template = S["MessagingTemplateOut"];
export type Settings = S["MessagingSettingsOut"];
export type GuardianOptins = S["GuardianOptinsOut"];
export type CategoryState = S["CategoryStateOut"];
export type Message = S["MessagingMessageOut"];
export type MessagePage = S["MessagingMessagePage"];
export type MessageStatus = Message["status"];
export type MessagePurpose = Message["purpose"];
export type BroadcastPreview = S["BroadcastPreviewOut"];
export type Broadcast = S["BroadcastOut"];
export type BroadcastPage = S["BroadcastPage"];
export type BroadcastReport = S["BroadcastReportOut"];
export type Automatic = S["AutomaticOut"];
export type SendRun = S["SendRunOut"];
/** R2-3J (FR-MSG-025): manual fee reminders from the dues list. */
export type FeeReminderSelection = S["FeeReminderSelectionIn"];
export type FeeReminderPreview = S["FeeReminderPreviewOut"];
export type FeeRemindersSent = S["FeeRemindersOut"];
/** R2-4 (FR-MSG-027): "Send payment link" is the same dialog with kind payment_link. */
export type FeeMessageKind = NonNullable<FeeReminderSelection["kind"]>;
/** R2-3J (FR-MSG-026): "Send on WhatsApp" for one receipt. */
export type ReceiptWhatsApp = S["ReceiptWhatsAppOut"];
/** R2-4: "Stop the link" of one sent message (ADR-0046 decision 5). */
export type RevokeLinkOut = S["RevokeLinkOut"];

export const MESSAGE_STATUSES: readonly MessageStatus[] = [
  "queued",
  "sent",
  "delivered",
  "read",
  "failed",
  "skipped",
];
export const MESSAGE_PURPOSES: readonly MessagePurpose[] = [
  "optin_confirm",
  "school_notice",
  "absence_alert",
  "fee_reminder",
  "fee_receipt",
  "optin_stopped",
];

export const KEYS = {
  all: ["staff", "messaging"] as const,
  account: ["staff", "messaging", "account"] as const,
  settings: ["staff", "messaging", "settings"] as const,
  optins: (guardianId: string) => ["staff", "messaging", "optins", guardianId] as const,
  messages: (filter: string) => ["staff", "messaging", "messages", filter] as const,
  broadcasts: ["staff", "messaging", "broadcasts"] as const,
  broadcast: (id: string) => ["staff", "messaging", "broadcasts", id] as const,
  noticeBroadcasts: (noticeId: string) =>
    ["staff", "messaging", "broadcasts", "notice", noticeId] as const,
  automatic: ["staff", "messaging", "automatic"] as const,
  feeReminderPreview: (selection: string) =>
    ["staff", "messaging", "fee-reminders", "preview", selection] as const,
  receipt: (receiptId: string) => ["staff", "messaging", "receipt", receiptId] as const,
} as const;

/** While a broadcast is sending, its counts are asked for again this often (live report). */
export const LIVE_REFRESH_MS = 5000;

/** `W/"3"` for If-Match (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

/** True when a failed load means "WhatsApp is not offered here" (404 `module_unavailable`). */
export function isUnavailable(loadable: Loadable<unknown>): boolean {
  return loadable.status === "error" && loadable.reason === "not_found";
}

export function useAccount(enabled = true): Loadable<Account> {
  const api = useBffClient("staff");
  return useApiQuery(KEYS.account, () => unwrap(api.GET("/api/v1/messaging/account")), {
    enabled,
  });
}

export function useMessagingSettings(enabled = true): Loadable<Settings> {
  const api = useBffClient("staff");
  return useApiQuery(KEYS.settings, () => unwrap(api.GET("/api/v1/messaging/settings")), {
    enabled,
  });
}

export function useGuardianOptins(guardianId: string, enabled = true): Loadable<GuardianOptins> {
  const api = useBffClient("staff");
  return useApiQuery(
    KEYS.optins(guardianId),
    () =>
      unwrap(
        api.GET("/api/v1/guardians/{guardian_id}/optins", {
          params: { path: { guardian_id: guardianId } },
        }),
      ),
    { enabled },
  );
}

/** Print view (A4, the API's own strict CSP) through the BFF in a new tab; ids only in the URL. */
export function sectionSlipsHref(sectionId: string, categories: readonly Category[]): string {
  const query = categories.map((c) => `categories=${encodeURIComponent(c)}`).join("&");
  return `/bff/api/v1/sections/${encodeURIComponent(sectionId)}/join-slips?${query}`;
}

/** Meta's ids are digits only; the token is the text Meta shows once (never stored here). */
const metaId = z
  .string()
  .trim()
  .regex(/^[0-9]{1,32}$/, { error: "invalidNumber" });

export const manualSchema = z.object({
  token: z.string().trim().min(20, { error: "tooShort" }).max(1024),
  waba_id: metaId,
  phone_number_id: metaId,
});

/** A Toggle with a `name` submits "on"/"off". */
const onOff = z.enum(["on", "off"]).transform((value) => value === "on");

/** 10 digits (an 11-digit landline with its STD code is allowed too); empty = none. */
export const settingsSchema = z.object({
  notices_enabled: onOff,
  absence_alerts_enabled: onOff,
  fee_reminders_enabled: onOff,
  fee_receipts_enabled: onOff,
  absence_alert_time: z.string().regex(/^[0-2][0-9]:[0-5][0-9](:[0-5][0-9])?$/),
  quiet_start: z.string().regex(/^[0-2][0-9]:[0-5][0-9](:[0-5][0-9])?$/),
  quiet_end: z.string().regex(/^[0-2][0-9]:[0-5][0-9](:[0-5][0-9])?$/),
  daily_cap: z
    .string()
    .trim()
    .regex(/^[0-9]{0,7}$/, { error: "invalidNumber" }),
  office_phone: z
    .string()
    .trim()
    .regex(/^(\+?91[\s-]?)?[0-9\s-]{10,13}$|^$/, { error: "invalidPhone" }),
  no_message_dates: z
    .string()
    .trim()
    .refine((value) => parseDayList(value) !== null, { error: "invalidDate" }),
});

const DAY = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/;

/**
 * "26/01/2027, 15/08/2027" (DD/MM/YYYY, commas or new lines) → ["2027-01-26", "2027-08-15"]
 * (sorted, no repeats); `null` when any entry is not a real date or there are more than 60.
 */
export function parseDayList(value: string): string[] | null {
  const parts = value
    .split(/[,\n]/)
    .map((part) => part.trim())
    .filter((part) => part !== "");
  const out = new Set<string>();
  for (const part of parts) {
    const match = DAY.exec(part);
    if (!match) return null;
    const [, d = "", m = "", y = ""] = match;
    const iso = `${y}-${m.padStart(2, "0")}-${d.padStart(2, "0")}`;
    const date = new Date(`${iso}T00:00:00Z`);
    if (Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== iso) return null;
    out.add(iso);
  }
  if (out.size > 60) return null;
  return [...out].sort();
}

/** ["2027-01-26"] → "26/01/2027" for the form. */
export function formatDayList(days: readonly string[]): string {
  return days.map((iso) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}`).join(", ");
}

export const optinSchema = z
  .object({
    category: z.enum(CATEGORIES),
    choice: z.enum(["signed_form", "in_person", "staff_recorded_stop"]),
    decided_on: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, { error: "invalidDate" }),
    evidence_document_id: z.string().trim(),
  })
  .refine((v) => v.choice !== "signed_form" || /^[0-9a-f-]{36}$/i.test(v.evidence_document_id), {
    path: ["evidence_document_id"],
    error: "required",
  });

/** "HH:MM" for a time input from the API's "HH:MM:SS". */
export function hhmm(value: string | null | undefined): string {
  return (value ?? "").slice(0, 5);
}

/** The settings body from the form (empty cap and phone mean "none"). */
export function settingsBody(input: z.output<typeof settingsSchema>) {
  return {
    ...input,
    absence_alert_time: hhmm(input.absence_alert_time) + ":00",
    quiet_start: hhmm(input.quiet_start) + ":00",
    quiet_end: hhmm(input.quiet_end) + ":00",
    daily_cap: input.daily_cap === "" ? null : Number(input.daily_cap),
    office_phone: input.office_phone === "" ? null : input.office_phone,
    no_message_dates: parseDayList(input.no_message_dates) ?? [],
  };
}

/** Estimated Meta cost of `count` utility messages incl. GST, in rupees (display only, D27). */
export function estimatedCost(count: number, rate: string, gstPercent: string): number {
  const perMessage = Number(rate) * (1 + Number(gstPercent) / 100);
  return Math.round(count * perMessage * 100) / 100;
}

export function useAutomatic(enabled = true): Loadable<Automatic> {
  const api = useBffClient("staff");
  return useApiQuery(KEYS.automatic, () => unwrap(api.GET("/api/v1/messaging/automatic")), {
    enabled,
  });
}

/** Messages that reached the parent's phone (delivered or read) out of those queued. */
export function deliveredCount(counts: Record<string, number>): number {
  return (counts.delivered ?? 0) + (counts.read ?? 0);
}

/** Every message of a broadcast (any status). */
export function totalCount(counts: Record<string, number>): number {
  return Object.values(counts).reduce((sum, n) => sum + n, 0);
}
