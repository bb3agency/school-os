import "server-only";
import { platformEnabled } from "@/server/session/rsc";

/**
 * Public-site settings (docs/17 §5.5), read on the server at request time, never inlined into a
 * browser bundle. Contact details are not known yet: when a value is unset (or invalid) the
 * part of the page that needs it is simply not shown. There is no placeholder anywhere.
 *
 * - `SOS_PUBLIC_CONTACT_EMAIL`   the "Talk to us" address (a mailto link; no form, no lead data)
 * - `SOS_PUBLIC_COMPANY_NAME`    legal or trading name for the footer and the About page
 * - `SOS_PUBLIC_COMPANY_ADDRESS` optional postal address for the About page (`\n` or `|` for lines)
 */
export interface MarketingSettings {
  contactEmail: string | null;
  companyName: string | null;
  companyAddress: string[] | null;
}

type Env = Readonly<Record<string, string | undefined>>;

/** A plain address: one @, no spaces, no characters that could break out of a mailto URL. */
const EMAIL = /^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;
const MAX_TEXT = 200;

function text(value: string | undefined): string | null {
  const trimmed = value?.trim().normalize("NFC");
  if (!trimmed || trimmed.length > MAX_TEXT) return null;
  return trimmed;
}

export function readMarketingSettings(env: Env = process.env): MarketingSettings {
  const email = env.SOS_PUBLIC_CONTACT_EMAIL?.trim() ?? "";
  const address = text(env.SOS_PUBLIC_COMPANY_ADDRESS?.replace(/\\n/g, "\n"));
  const lines = address
    ?.split(/\n|\|/)
    .map((line) => line.trim())
    .filter(Boolean);
  return {
    contactEmail: email.length <= 254 && EMAIL.test(email) ? email : null,
    companyName: text(env.SOS_PUBLIC_COMPANY_NAME),
    companyAddress: lines && lines.length > 0 ? lines : null,
  };
}

/**
 * The public marketing pages exist only on the shared SaaS. A dedicated host
 * (`SOS_DEPLOYMENT_MODE=dedicated`) is one school's own address: Features, Security,
 * Pricing and About answer 404 there and /welcome is a plain sign-in page.
 */
export function marketingEnabled(): boolean {
  return platformEnabled();
}
