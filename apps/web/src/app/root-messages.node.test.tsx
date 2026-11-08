// @vitest-environment node
import { NextIntlClientProvider } from "next-intl";
import { Children, isValidElement, type ReactElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { CLIENT_NAMESPACES } from "@/i18n/client-messages";

/**
 * WP-04 (audit 2026-11-08): the `[locale]` root layout sends only the shared message
 * namespaces to the browser, not the whole catalogue (560 KB of en.json in every page before).
 * The returned element is inspected, not rendered.
 */

vi.mock("next/headers", () => ({ headers: async () => new Headers({ "x-nonce": "n" }) }));
vi.mock("next/server", () => ({ connection: async () => undefined }));
vi.mock("next-intl/server", () => ({
  setRequestLocale: () => undefined,
  getTranslations: async () => (key: string) => key,
}));

import LocaleLayout from "./[locale]/layout";

function find(node: ReactNode, match: (el: ReactElement) => boolean): ReactElement | null {
  for (const child of Children.toArray(node)) {
    if (!isValidElement(child)) continue;
    if (match(child)) return child;
    const found = find((child.props as { children?: ReactNode }).children, match);
    if (found) return found;
  }
  return null;
}

describe("root layout messages (WP-04)", () => {
  it("passes only the root namespaces to NextIntlClientProvider", async () => {
    const tree = await LocaleLayout({ children: "x", params: Promise.resolve({ locale: "en" }) });
    const provider = find(tree, (el) => el.type === NextIntlClientProvider) as ReactElement<{
      messages?: Record<string, unknown>;
    }> | null;
    expect(provider).not.toBeNull();
    const keys = Object.keys(provider?.props.messages ?? {}).sort();
    expect(keys).toEqual([...CLIENT_NAMESPACES.root].sort());
    expect(keys).not.toContain("platform");
    expect(keys).not.toContain("fees");
  });
});
