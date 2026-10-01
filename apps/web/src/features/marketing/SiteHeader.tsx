"use client";

import { AnimatePresence, LazyMotion, MotionConfig, m } from "motion/react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { BrandMark } from "@/components/shell/Brand";
import { buttonClasses } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { EASE_OUT, loadMotionFeatures, seconds } from "@/lib/motion";
import { Presence } from "@/lib/presence";
import { WhatsAppLink } from "./WhatsAppLink";
import { MARKETING_PAGES, PAGE_HREF, SIGN_IN_HREF, mailtoHref, type MarketingPage } from "./links";

const WIDE = "(min-width: 64rem)";

/** "Has the page scrolled?" without an effect that sets state (server snapshot: no). */
function subscribeScroll(callback: () => void) {
  window.addEventListener("scroll", callback, { passive: true });
  return () => window.removeEventListener("scroll", callback);
}
const scrolledSnapshot = () => window.scrollY > 8;
const serverSnapshot = () => false;

/**
 * Sticky header of the public pages (docs/17 §5.6): wordmark, the four pages, "Sign in" and,
 * when a contact address is configured, "Talk to us". From lg the links sit in the bar; below
 * lg a "Menu" button opens them as a disclosure panel: focus moves to the first link, Escape
 * or a second press closes it and focus returns to the button; following a link, widening
 * the window, a click outside the header or Tab moving focus out of it closes it too (the
 * panel covers the top of the page, so it must never hide the control that has focus,
 * WCAG 2.4.11). The panel enters from the button's corner (Motion, 200ms ease-out; out in
 * 150ms, `inert` while it fades out) and is rendered only while open, so the server markup
 * never carries a style attribute (CSP).
 */
export function SiteHeader({
  current,
  contactEmail,
  whatsappNumber = null,
}: {
  current: MarketingPage;
  contactEmail: string | null;
  /** "Ask on WhatsApp": in the bar from xl (room beside the page links), in the menu below lg. */
  whatsappNumber?: string | null;
}) {
  const t = useTranslations("marketing");
  const tc = useTranslations("common");
  const [open, setOpen] = useState(false);
  const scrolled = useSyncExternalStore(subscribeScroll, scrolledSnapshot, serverSnapshot);
  const headerRef = useRef<HTMLElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) buttonRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>("a")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(true);
    };
    const wide = window.matchMedia(WIDE);
    const onWide = () => {
      if (wide.matches) close(false);
    };
    const outside = (target: EventTarget | null) =>
      target instanceof Node && !headerRef.current?.contains(target);
    const onPointer = (event: PointerEvent) => {
      if (outside(event.target)) close(false);
    };
    const onFocusOut = (event: FocusEvent) => {
      if (outside(event.relatedTarget)) close(false);
    };
    const header = headerRef.current;
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    header?.addEventListener("focusout", onFocusOut);
    wide.addEventListener("change", onWide);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
      header?.removeEventListener("focusout", onFocusOut);
      wide.removeEventListener("change", onWide);
    };
  }, [open, close]);

  const navLink = (page: (typeof MARKETING_PAGES)[number], big = false) => (
    <Link
      href={PAGE_HREF[page]}
      aria-current={current === page ? "page" : undefined}
      onClick={big ? () => close(false) : undefined}
      className={cn(
        "inline-flex items-center rounded-md font-medium motion-safe:transition-colors",
        big
          ? "min-h-12 w-full px-3 text-base hover:bg-surface-muted"
          : "min-h-10 px-3 text-sm hover:bg-surface-muted/70",
        current === page ? "text-ink" : "text-ink-muted hover:text-ink",
      )}
    >
      {t(`nav.${page}`)}
      {current === page ? (
        <span aria-hidden="true" className="ms-2 size-1.5 rounded-full bg-primary" />
      ) : null}
    </Link>
  );

  return (
    <LazyMotion features={loadMotionFeatures} strict>
      <MotionConfig reducedMotion="user">
        <header
          ref={headerRef}
          className="mk-header sticky top-0 z-40"
          data-scrolled={scrolled ? "true" : "false"}
          data-open={open ? "true" : "false"}
        >
          <div className="px-page mx-auto flex h-full w-full max-w-7xl items-center justify-between gap-4">
            <Link
              href={PAGE_HREF.home}
              className="mk-press inline-flex min-h-11 items-center gap-2.5 rounded-full pe-2"
              aria-current={current === "home" ? "page" : undefined}
            >
              <BrandMark />
              <span className="text-lg font-semibold tracking-tight text-ink">{tc("appName")}</span>
            </Link>

            <nav aria-label={t("nav.label")} className="hidden lg:block" data-print="hide">
              <ul className="flex items-center gap-1">
                {MARKETING_PAGES.map((page) => (
                  <li key={page}>{navLink(page)}</li>
                ))}
              </ul>
            </nav>

            <div className="flex items-center gap-2" data-print="hide">
              <a
                href={SIGN_IN_HREF}
                className="mk-press hidden min-h-10 items-center rounded-md px-4 text-sm font-medium text-ink hover:bg-surface-muted motion-safe:transition-colors sm:inline-flex"
              >
                {t("cta.signIn")}
              </a>
              {whatsappNumber ? (
                <span className="hidden xl:contents">
                  <WhatsAppLink number={whatsappNumber} size="md" />
                </span>
              ) : null}
              {contactEmail ? (
                <span className="hidden sm:contents">
                  <a
                    href={mailtoHref(contactEmail)}
                    className={cn(buttonClasses("primary", "md"), "mk-press")}
                  >
                    {t("cta.talk")}
                  </a>
                </span>
              ) : null}
              <button
                ref={buttonRef}
                type="button"
                className="mk-press inline-flex min-h-11 items-center gap-2 rounded-full border border-border-soft bg-surface px-4 text-sm font-medium text-ink lg:hidden"
                aria-expanded={open}
                aria-controls={panelId}
                onClick={() => (open ? close(true) : setOpen(true))}
              >
                <Icon name={open ? "close" : "menu"} className="size-4.5" />
                {open ? t("nav.closeMenu") : t("nav.menu")}
              </button>
            </div>
          </div>

          <AnimatePresence>
            {open ? (
              <Presence key="menu">
                {(exiting) => (
                  <m.div
                    inert={exiting}
                    ref={panelRef}
                    id={panelId}
                    className="px-page absolute inset-x-0 top-full origin-top-right pt-2 lg:hidden"
                    initial={{ opacity: 0, transform: "translateY(-6px) scale(0.98)" }}
                    animate={{ opacity: 1, transform: "translateY(0px) scale(1)" }}
                    exit={{
                      opacity: 0,
                      transform: "translateY(-4px) scale(0.98)",
                      transition: { duration: seconds("quick"), ease: EASE_OUT },
                    }}
                    transition={{ duration: seconds("enter"), ease: EASE_OUT }}
                  >
                    <div className="mx-auto max-w-7xl rounded-xl border border-border bg-surface p-3 shadow-popover">
                      <nav aria-label={t("nav.label")}>
                        <ul className="grid gap-0.5">
                          {MARKETING_PAGES.map((page) => (
                            <li key={page}>{navLink(page, true)}</li>
                          ))}
                        </ul>
                      </nav>
                      <div className="mt-3 grid gap-2 border-t border-border pt-3 sm:grid-cols-2">
                        {contactEmail ? (
                          <a
                            href={mailtoHref(contactEmail)}
                            className={cn(buttonClasses("primary", "lg"), "mk-press w-full")}
                          >
                            {t("cta.talk")}
                          </a>
                        ) : null}
                        {whatsappNumber ? (
                          <WhatsAppLink number={whatsappNumber} className="w-full" />
                        ) : null}
                        <a
                          href={SIGN_IN_HREF}
                          className={cn(buttonClasses("secondary", "lg"), "mk-press w-full")}
                        >
                          {t("cta.signIn")}
                        </a>
                      </div>
                    </div>
                  </m.div>
                )}
              </Presence>
            ) : null}
          </AnimatePresence>
        </header>
      </MotionConfig>
    </LazyMotion>
  );
}
