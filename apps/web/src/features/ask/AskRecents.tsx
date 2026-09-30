"use client";

import { useTranslations } from "next-intl";
import { useId } from "react";
import { Icon, type IconName } from "@/components/ui/Icon";
import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { NEW_CHAT_EVENT } from "./chat";
import { RECENT_COUNT, titleOf, useConversationList } from "./conversations";

const row =
  "relative flex min-h-9 items-center gap-2 rounded-md px-2 py-1.5 text-sm text-ink-muted hover:bg-surface-muted hover:text-ink";
const current =
  "bg-primary-soft font-semibold text-primary hover:bg-primary-soft hover:text-primary";

function Bar() {
  return (
    <span
      aria-hidden="true"
      className="absolute inset-y-1.5 start-0 w-[3px] rounded-full bg-primary"
    />
  );
}

function SubLink({
  href,
  icon,
  label,
  active,
  onClick,
}: {
  href: string;
  icon: IconName;
  label: string;
  active: boolean;
  onClick?: () => void;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      onClick={onClick}
      className={cn(row, active && current)}
    >
      {active ? <Bar /> : null}
      <Icon name={icon} className="size-4" />
      <span className="min-w-0 flex-1 break-words">{label}</span>
    </Link>
  );
}

/**
 * Ask the school's sub-list in the one sidebar (docs/17 §5.2, §5.3): "New chat", the recent
 * chats (pinned first, at most eight, the open one marked current; a long title is cut with
 * "…" and its full text stays the link's name and tooltip), "All chats" and "Memory". Shown
 * while an Ask page is open, so the menu stays short elsewhere; hidden in the compact sidebar.
 * The list is the same cached query as the All chats page, so renames, pins and deletes show
 * here at once.
 */
export function AskRecents() {
  const t = useTranslations("ask");
  const pathname = usePathname() ?? "";
  const inAsk = pathname === "/ask" || pathname.startsWith("/ask/");
  const list = useConversationList(inAsk);
  const headingId = useId();
  if (!inAsk) return null;
  const recent = list.items.slice(0, RECENT_COUNT);
  return (
    <div
      className="mt-1 ms-4 space-y-0.5 border-s border-border ps-2 collapsed:hidden"
      data-print="hide"
    >
      <SubLink
        href="/ask"
        icon="newChat"
        label={t("chat.newChat")}
        active={false}
        onClick={() => window.dispatchEvent(new Event(NEW_CHAT_EVENT))}
      />
      {recent.length > 0 ? (
        <>
          <p id={headingId} className="eyebrow px-2 pt-2 pb-1 text-ink-subtle">
            {t("chat.recent")}
          </p>
          <ul aria-labelledby={headingId} className="space-y-0.5">
            {recent.map((item) => {
              const title = titleOf(item) ?? t("chat.untitled");
              const href = `/ask/c/${item.id}`;
              const active = pathname === href;
              return (
                <li key={item.id} className="chat-enter">
                  <Link
                    href={href}
                    title={title}
                    aria-current={active ? "page" : undefined}
                    className={cn(row, "py-1", active && current)}
                  >
                    {active ? <Bar /> : null}
                    {item.pinned ? (
                      <>
                        <Icon name="pin" className="size-3.5 shrink-0" />
                        <span className="sr-only">{`${t("chat.pinnedMark")}: `}</span>
                      </>
                    ) : null}
                    <span className="min-w-0 flex-1 truncate leading-relaxed">{title}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
      <SubLink
        href="/ask/history"
        icon="clock"
        label={t("chat.allChats")}
        active={pathname === "/ask/history"}
      />
      <SubLink
        href="/ask/memory"
        icon="memory"
        label={t("memory.nav")}
        active={pathname === "/ask/memory"}
      />
    </div>
  );
}
