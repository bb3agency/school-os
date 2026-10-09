"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { cardClasses } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { SearchInput } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Link } from "@/i18n/navigation";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { useConversationActions } from "./ConversationMenu";
import { titleOf, useConversationList } from "./conversations";
import { ASK_PERM, type ConversationSummary } from "./data";
import { ActionButton } from "./MessageActions";
import { AskTabs } from "./parts";

/** Case- and accent-insensitive match of every word (English and Telugu titles alike). */
export function matchesFilter(title: string, filter: string): boolean {
  const norm = (value: string) => value.normalize("NFC").toLocaleLowerCase();
  const haystack = norm(title);
  return norm(filter)
    .split(/\s+/u)
    .filter(Boolean)
    .every((word) => haystack.includes(word));
}

function Row({
  conversation,
  onPin,
  onRename,
  onDelete,
}: {
  conversation: ConversationSummary;
  onPin: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  const t = useTranslations("ask.history");
  const tc = useTranslations("ask.chat");
  const title = titleOf(conversation) ?? tc("untitled");
  const updated = formatDate(conversation.updated_at);
  return (
    <li className="chat-enter flex items-center gap-2 px-3 py-2.5 sm:px-4">
      <div className="min-w-0 flex-1">
        <Link
          href={`/ask/c/${conversation.id}`}
          className="block font-semibold text-ink break-anywhere hover:text-primary hover:underline"
        >
          {conversation.pinned ? (
            <Icon name="pin" className="me-1.5 inline size-4 align-[-0.125em] text-primary" />
          ) : null}
          {conversation.pinned ? <span className="sr-only">{`${tc("pinnedMark")}: `}</span> : null}
          {title}
        </Link>
        <p className="text-xs text-ink-subtle">
          {updated ? t("updated", { date: updated }) : null}
          {updated ? " · " : null}
          {t("messages", { count: conversation.message_count })}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        <ActionButton
          icon="pin"
          label={conversation.pinned ? t("unpinFor", { title }) : t("pinFor", { title })}
          pressed={conversation.pinned}
          onClick={onPin}
        />
        <ActionButton icon="pencil" label={t("renameFor", { title })} onClick={onRename} />
        <ActionButton icon="trash" label={t("deleteFor", { title })} onClick={onDelete} />
      </div>
    </li>
  );
}

/**
 * All chats (FR-KB-012): every conversation of the signed-in member, pinned first then the
 * newest, loaded page by page (cursor; the next page loads as the end comes into view, and
 * with the button). Find filters the loaded titles as you type (no request). Pin, rename and
 * delete apply at once and are undone if the API refuses.
 */
export function HistoryScreen() {
  const t = useTranslations("ask.history");
  const ta = useTranslations("ask");
  const tc = useTranslations("common");
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const allowed = can(ASK_PERM.ask);
  const list = useConversationList(allowed);
  const actions = useConversationActions();
  const [filter, setFilter] = useState("");
  const sentinel = useRef<HTMLDivElement>(null);
  const pinnedId = useId();
  const othersId = useId();

  const matching = useMemo(
    () =>
      filter.trim()
        ? list.items.filter((item) => matchesFilter(titleOf(item) ?? ta("chat.untitled"), filter))
        : list.items,
    [list.items, filter, ta],
  );
  const pinned = matching.filter((item) => item.pinned);
  const others = matching.filter((item) => !item.pinned);

  // The next page loads when the end of the list comes into view (the button stays too).
  const { hasMore, loadMore } = list;
  useEffect(() => {
    const node = sentinel.current;
    if (!node || !hasMore || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadMore();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMore, loadMore]);

  if (me.isPending) return <LoadingState label={tc("loading")} />;

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[
        { label: ta("home"), href: "/" },
        { label: ta("title"), href: "/ask" },
        { label: t("title") },
      ]}
      actions={
        allowed ? (
          <ButtonLink href="/ask">
            <Icon name="newChat" className="size-4" />
            {ta("chat.newChat")}
          </ButtonLink>
        ) : null
      }
    />
  );

  if (!allowed) {
    return (
      <div className="space-y-6">
        {header}
        <Alert tone="warning" title={ta("noAccessTitle")}>
          {ta("noAccessBody")}
        </Alert>
      </div>
    );
  }

  const section = (id: string, label: string, items: ConversationSummary[]) =>
    items.length > 0 ? (
      <section aria-labelledby={id} className="space-y-2">
        <h2 id={id} className="eyebrow px-1 text-ink-subtle">
          {label}
        </h2>
        <ul className={`${cardClasses({ padding: "none" })} divide-y divide-border`}>
          {items.map((item) => (
            <Row
              key={item.id}
              conversation={item}
              onPin={() => actions.togglePin(item)}
              onRename={() => actions.rename(item)}
              onDelete={() => actions.remove(item)}
            />
          ))}
        </ul>
      </section>
    ) : null;

  return (
    <div className="space-y-6">
      {header}
      <AskTabs active="ask" />
      {actions.dialogs}
      {list.isPending ? (
        <LoadingState label={tc("loading")} rows={5} />
      ) : list.isError ? (
        <ApiErrorAlert error={list.error} namespace="ask" />
      ) : list.items.length === 0 ? (
        <EmptyState
          icon="message"
          title={t("emptyTitle")}
          body={t("emptyBody")}
          action={<ButtonLink href="/ask">{ta("chat.newChat")}</ButtonLink>}
        />
      ) : (
        <>
          <div className="max-w-md space-y-1">
            <SearchInput
              label={t("filterLabel")}
              labelVisible
              placeholder={t("filterPlaceholder")}
              value={filter}
              onChange={(event) => setFilter(event.currentTarget.value)}
              autoComplete="off"
            />
            {filter.trim() ? (
              <p role="status" className="text-sm text-ink-muted">
                {t("filterCount", { count: matching.length, total: list.items.length })}
              </p>
            ) : null}
          </div>
          {matching.length === 0 ? (
            <EmptyState icon="search" title={t("filterEmptyTitle")} body={t("filterEmptyBody")} />
          ) : (
            <div className="space-y-6">
              {section(pinnedId, t("pinned"), pinned)}
              {section(othersId, t("others"), others)}
            </div>
          )}
          {list.hasMore ? (
            <div ref={sentinel} className="flex justify-center">
              <Button variant="secondary" onClick={list.loadMore} disabled={list.loadingMore}>
                {list.loadingMore ? t("loadingMore") : t("loadMore")}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
