"use client";

import { useLocale, useTranslations } from "next-intl";
import { useRef, type ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { buttonClasses } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { Menu, type MenuEntry } from "@/components/ui/Menu";
import type { Locale } from "@/i18n/routing";
import { formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { isFeesOff, printHref } from "./data";

/** Loading, "fees are not switched on" (404 module_hidden), an error, or the content. */
export function FeesGate<T>({
  data,
  children,
}: {
  data: Loadable<T>;
  children: (value: T) => ReactNode;
}) {
  const t = useTranslations("fees");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  if (data.status === "loading") return <LoadingState label={tc("loading")} />;
  if (isFeesOff(data)) {
    return (
      <Alert tone="info" title={t("off.title")}>
        {t("off.body")}
      </Alert>
    );
  }
  if (data.status === "unavailable") {
    return (
      <Alert tone="info" title={tc("notAvailableYetTitle")}>
        {tc("notAvailableYetBody")}
      </Alert>
    );
  }
  if (data.status === "error") {
    return (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {data.reason ? te(`load.${data.reason}`) : tc("loadErrorBody")}
      </Alert>
    );
  }
  return <>{children(data.data)}</>;
}

/** Rupees in Indian grouping (₹1,25,000.00) in the figures font, never wrapped or cut. */
export function Amount({
  value,
  className,
}: {
  value: string | null | undefined;
  className?: string;
}) {
  const locale = useLocale() as Locale;
  return (
    <span className={className ?? "num whitespace-nowrap"}>
      {formatInr(value ?? null, locale) ?? "—"}
    </span>
  );
}

const RECEIPT_PILL: Record<string, PillVariant> = { issued: "done", cancelled: "negative" };
const REQUEST_PILL: Record<string, PillVariant> = {
  pending: "progress",
  approved: "done",
  processed: "done",
  rejected: "negative",
  withdrawn: "tag",
  processing: "progress",
  failed: "negative",
};

export function ReceiptStatusPill({ status }: { status: string }) {
  const t = useTranslations("fees.receiptStatus");
  return <Pill variant={RECEIPT_PILL[status] ?? "tag"}>{t(status as "issued")}</Pill>;
}

export function RequestStatusPill({ status }: { status: string }) {
  const t = useTranslations("fees.requestStatus");
  return <Pill variant={REQUEST_PILL[status] ?? "tag"}>{t(status as "pending")}</Pill>;
}

export function ModeText({ mode }: { mode: string }) {
  const t = useTranslations("fees.modes");
  return <>{t(mode as "cash")}</>;
}

/** Opens an API print page (strict CSP, no scripts) in a new tab through the BFF. */
export function PrintLink({ path, label }: { path: string; label: string }) {
  const t = useTranslations("fees");
  return (
    <a
      href={printHref(path)}
      target="_blank"
      rel="noopener noreferrer"
      className={buttonClasses("secondary", "md")}
    >
      <Icon name="file" className="size-4" />
      {label}
      <span className="sr-only"> ({t("newTab")})</span>
    </a>
  );
}

/** One item of a page's "More" menu: an action, a link, or a confirm dialog it opens. */
export type MoreItem =
  | {
      id: string;
      label: string;
      onSelect: () => void;
      danger?: boolean;
      disabled?: boolean;
    }
  | { id: string; label: string; href: string }
  | {
      id: string;
      label: string;
      danger?: boolean;
      /** An `ActionDialog` with `triggerHidden`; the menu item opens it. */
      dialog: ReactNode;
    };

/**
 * The secondary actions of a page header under one "More" button (docs/17 §5). Confirm
 * dialogs stay `ActionDialog`s (consequence line, step-up, idempotency key): each is mounted
 * with its trigger hidden, the menu item presses that trigger, and when the dialog closes focus
 * returns to the "More" button. Renders nothing without items.
 */
export function MoreActions({ label, items }: { label: string; items: readonly MoreItem[] }) {
  const tu = useTranslations("ui");
  const root = useRef<HTMLDivElement>(null);
  if (items.length === 0) return null;
  function launch(id: string) {
    const host = root.current?.querySelector(`[data-more-dialog="${id}"]`);
    const trigger = host?.querySelector<HTMLButtonElement>('button[aria-haspopup="dialog"]');
    host?.querySelector("dialog")?.addEventListener(
      "close",
      () => {
        window.setTimeout(() => {
          root.current?.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"]')?.focus();
        }, 0);
      },
      { once: true },
    );
    trigger?.click();
  }
  const entries: MenuEntry[] = items.map((item): MenuEntry => {
    if ("href" in item) return { kind: "link", id: item.id, label: item.label, href: item.href };
    if ("dialog" in item) {
      return {
        id: item.id,
        label: item.label,
        danger: item.danger === true,
        onSelect: () => launch(item.id),
      };
    }
    return {
      id: item.id,
      label: item.label,
      danger: item.danger === true,
      disabled: item.disabled === true,
      onSelect: item.onSelect,
    };
  });
  return (
    <div ref={root} className="contents">
      <Menu
        label={label}
        variant="button"
        trigger={
          <>
            {tu("more")}
            <Icon name="chevronDown" className="size-4" />
          </>
        }
        entries={entries}
      />
      {items.map((item) =>
        "dialog" in item ? (
          <div key={item.id} data-more-dialog={item.id} className="contents">
            {item.dialog}
          </div>
        ) : null,
      )}
    </div>
  );
}

/** "Who cannot see this" note for people without the permission (no API call made). */
export function NoAccess() {
  const t = useTranslations("fees");
  return (
    <Alert tone="info" title={t("noAccessTitle")}>
      {t("noAccessBody")}
    </Alert>
  );
}
