"use client";

/**
 * Meta's Embedded Signup (v4) on the WhatsApp settings page only (ADR-0043 decision 5;
 * FR-MSG-002; docs/10 §11.2: the only page that loads Meta's SDK, with a CSP exception and
 * `Cross-Origin-Opener-Policy: same-origin-allow-popups` scoped to `/settings/whatsapp`).
 *
 * The school's owner signs in to Meta in Meta's own window and picks or creates the business
 * account and number. Meta then posts the WABA id and phone number id to this window
 * (`WA_EMBEDDED_SIGNUP` / `FINISH`) and returns a one-time code; the page sends the three to
 * `POST /messaging/account/embedded-signup`, where the API exchanges the code for the school's
 * business token. No token ever reaches the browser.
 */

export const SDK_URL = "https://connect.facebook.net/en_US/sdk.js";
const META_ORIGIN = /^https:\/\/([a-z0-9-]+\.)*facebook\.com$/;

export interface SignupConfig {
  app_id: string;
  config_id: string;
  version: string;
  graph_version: string;
}

export interface SignupIds {
  waba_id: string;
  phone_number_id: string;
  business_id?: string;
}

export type SignupEvent =
  { kind: "finish"; ids: SignupIds } | { kind: "cancel" } | { kind: "error" } | { kind: "ignore" };

const DIGITS = /^[0-9]{1,32}$/;

/** Reads one `message` event: only Meta's origin and the documented shape count. */
export function parseSignupMessage(origin: string, data: unknown): SignupEvent {
  if (!META_ORIGIN.test(origin)) return { kind: "ignore" };
  let value: unknown = data;
  if (typeof data === "string") {
    try {
      value = JSON.parse(data);
    } catch {
      return { kind: "ignore" };
    }
  }
  if (!value || typeof value !== "object") return { kind: "ignore" };
  const item = value as { type?: unknown; event?: unknown; data?: unknown };
  if (item.type !== "WA_EMBEDDED_SIGNUP") return { kind: "ignore" };
  if (item.event === "CANCEL") return { kind: "cancel" };
  if (item.event === "ERROR") return { kind: "error" };
  if (item.event !== "FINISH" && item.event !== "FINISH_ONLY_WABA") return { kind: "ignore" };
  const ids = (item.data ?? {}) as Record<string, unknown>;
  const waba = ids.waba_id;
  const phone = ids.phone_number_id;
  if (typeof waba !== "string" || !DIGITS.test(waba)) return { kind: "error" };
  if (typeof phone !== "string" || !DIGITS.test(phone)) return { kind: "error" };
  const business = typeof ids.business_id === "string" && DIGITS.test(ids.business_id);
  return {
    kind: "finish",
    ids: {
      waba_id: waba,
      phone_number_id: phone,
      ...(business ? { business_id: ids.business_id as string } : {}),
    },
  };
}

interface FacebookSdk {
  init: (options: Record<string, unknown>) => void;
  login: (
    callback: (response: { authResponse?: { code?: string } | null }) => void,
    options: Record<string, unknown>,
  ) => void;
}

declare global {
  interface Window {
    FB?: FacebookSdk;
    fbAsyncInit?: () => void;
  }
}

let loading: Promise<FacebookSdk> | null = null;

/** Loads Meta's SDK once (a script created by this trusted bundle; CSP `strict-dynamic`). */
export function loadSdk(config: SignupConfig): Promise<FacebookSdk> {
  if (window.FB) return Promise.resolve(window.FB);
  if (loading) return loading;
  loading = new Promise<FacebookSdk>((resolve, reject) => {
    window.fbAsyncInit = () => {
      if (!window.FB) {
        reject(new Error("sdk_missing"));
        return;
      }
      window.FB.init({
        appId: config.app_id,
        autoLogAppEvents: false,
        xfbml: false,
        version: config.graph_version,
      });
      resolve(window.FB);
    };
    const script = document.createElement("script");
    script.src = SDK_URL;
    script.async = true;
    script.crossOrigin = "anonymous";
    script.onerror = () => {
      loading = null;
      reject(new Error("sdk_failed"));
    };
    document.body.appendChild(script);
  });
  return loading;
}

/**
 * Runs the sign-up: resolves with the code and ids once Meta reports both, rejects with
 * `cancelled` or `failed`. The listener is removed in every case.
 */
export async function runEmbeddedSignup(
  config: SignupConfig,
): Promise<{ code: string } & SignupIds> {
  const sdk = await loadSdk(config);
  return new Promise((resolve, reject) => {
    let ids: SignupIds | null = null;
    let code: string | null = null;
    const finish = () => {
      if (ids && code) {
        window.removeEventListener("message", onMessage);
        resolve({ code, ...ids });
      }
    };
    function onMessage(event: MessageEvent) {
      const parsed = parseSignupMessage(event.origin, event.data);
      if (parsed.kind === "finish") {
        ids = parsed.ids;
        finish();
      } else if (parsed.kind === "cancel" || parsed.kind === "error") {
        window.removeEventListener("message", onMessage);
        reject(new Error(parsed.kind === "cancel" ? "cancelled" : "failed"));
      }
    }
    window.addEventListener("message", onMessage);
    sdk.login(
      (response) => {
        const value = response.authResponse?.code;
        if (typeof value !== "string" || value.length < 8) {
          window.removeEventListener("message", onMessage);
          reject(new Error("cancelled"));
          return;
        }
        code = value;
        finish();
      },
      {
        config_id: config.config_id,
        response_type: "code",
        override_default_response_type: true,
        extras: { setup: {}, sessionInfoVersion: "3", version: config.version },
      },
    );
  });
}
