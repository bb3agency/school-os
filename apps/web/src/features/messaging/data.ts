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
export const MESSAGING_PERMISSIONS = [CONFIGURE, RECORD, READ] as const;
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
} as const;

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
});

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
  };
}

/** Estimated Meta cost of `count` utility messages incl. GST, in rupees (display only, D27). */
export function estimatedCost(count: number, rate: string, gstPercent: string): number {
  const perMessage = Number(rate) * (1 + Number(gstPercent) / 100);
  return Math.round(count * perMessage * 100) / 100;
}
