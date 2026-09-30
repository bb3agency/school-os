/**
 * Synthetic public-site settings (docs/10 §11, docs/17 §5.6) for the `chromium-contact` e2e
 * server (playwright.config.ts). Never real contact details: a reserved example domain
 * (RFC 2606), a made-up company and address, and a number that is not in use.
 */
export const E2E_PUBLIC_SETTINGS = {
  SOS_PUBLIC_CONTACT_EMAIL: "hello@example.com",
  SOS_PUBLIC_COMPANY_NAME: "Example Test Company",
  SOS_PUBLIC_COMPANY_ADDRESS: "1 Test Street|Test Town 500001",
  SOS_PUBLIC_WHATSAPP_NUMBER: "+910000000000",
} as const;

/** The digits the "Ask on WhatsApp" link must carry (country code first, no +). */
export const E2E_WHATSAPP_DIGITS = "910000000000";
