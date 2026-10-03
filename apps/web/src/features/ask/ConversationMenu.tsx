"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Icon, type IconName } from "@/components/ui/Icon";
import { Link, useRouter } from "@/i18n/navigation";
import { containsAadhaarNumber } from "@/lib/aadhaar";
import { cn } from "@/lib/cn";
import { ConfirmDialog } from "./ConfirmDialog";
import { titleOf, useDeleteConversation, useUpdateConversation } from "./conversations";
import type { ConversationSummary } from "./data";
import { askError, isExplained } from "./errors";

/** Longest title the API stores (ConversationPatchIn.title: 1-120 characters). */
export const TITLE_MAX = 120;

const itemClasses =
  "flex min-h-10 w-full items-center gap-3 rounded-md px-3 text-start text-sm text-ink hover:bg-surface-muted";

function Item({
  icon,
  label,
  onClick,
  danger,
}: {
  icon: IconName;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <li>
      <button type="button" onClick={onClick} className={cn(itemClasses, danger && "text-danger")}>
        <Icon name={icon} className="size-4" />
        {label}
      </button>
    </li>
  );
}

/** Rename, pin and delete a conversation with confirm dialogs; shared by the header and history. */
export function useConversationActions(onDeleted?: () => void) {
  const th = useTranslations("ask.history");
  const tc = useTranslations("ask.chat");
  const update = useUpdateConversation();
  const remove = useDeleteConversation();
  const [renaming, setRenaming] = useState<ConversationSummary | null>(null);
  const [deleting, setDeleting] = useState<ConversationSummary | null>(null);
  const tv = useTranslations("validation");
  const [notice, setNotice] = useState<string | null>(null);
  const [titleError, setTitleError] = useState<string | null>(null);
  const fieldId = useId();
  const errorId = useId();

  // The last refusal: a reason the API gave for a title (422 title_*) or a change made
  // elsewhere (412) is explained; anything else says the change was undone.
  const failure = update.error ?? remove.error;
  const dialogs = (
    <>
      <ConfirmDialog
        open={renaming !== null}
        title={th("rename")}
        confirmLabel={th("save")}
        confirmVariant="primary"
        onClose={() => {
          setRenaming(null);
          setTitleError(null);
        }}
        onConfirm={(form) => {
          const value = String(new FormData(form).get("title") ?? "")
            .trim()
            .slice(0, TITLE_MAX);
          if (!renaming || !value) return false;
          // Invariant 4: a full Aadhaar number never leaves the browser (the API refuses it too).
          if (containsAadhaarNumber(value)) {
            setTitleError(tv("noAadhaar"));
            return false;
          }
          if (value !== renaming.title) {
            update.mutate(
              { conversation: renaming, patch: { title: value } },
              { onSuccess: () => setNotice(th("renamed")) },
            );
          }
          return true;
        }}
      >
        <label htmlFor={fieldId} className="block pt-2 text-sm font-semibold">
          {th("renameLabel")}
        </label>
        <input
          id={fieldId}
          name="title"
          defaultValue={renaming ? (titleOf(renaming) ?? "") : ""}
          maxLength={TITLE_MAX}
          required
          aria-invalid={titleError ? true : undefined}
          aria-describedby={titleError ? errorId : undefined}
          onChange={() => setTitleError(null)}
          className="block min-h-10 w-full rounded-md border border-border-control bg-surface-muted px-3 text-base"
        />
        {titleError ? (
          <p id={errorId} role="alert" className="text-sm font-semibold text-danger">
            {titleError}
          </p>
        ) : null}
      </ConfirmDialog>
      <ConfirmDialog
        open={deleting !== null}
        title={th("deleteTitle")}
        body={th("deleteBody", { title: titleOf(deleting) ?? tc("untitled") })}
        confirmLabel={th("deleteConfirm")}
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          if (!deleting) return false;
          remove.mutate(deleting, { onSuccess: () => setNotice(th("deleted")) });
          onDeleted?.();
          return true;
        }}
      />
      <p role="status" className="sr-only">
        {notice}
      </p>
      {failure ? (
        isExplained(failure, "conversation_not_found") ? (
          <ApiErrorAlert error={askError(failure, "conversation_not_found")} namespace="ask" />
        ) : (
          <Alert tone="danger" live>
            {th("failed")}
          </Alert>
        )
      ) : null}
    </>
  );

  return {
    dialogs,
    rename: (conversation: ConversationSummary) => setRenaming(conversation),
    remove: (conversation: ConversationSummary) => setDeleting(conversation),
    togglePin: (conversation: ConversationSummary) =>
      update.mutate(
        { conversation, patch: { pinned: !conversation.pinned } },
        { onSuccess: () => setNotice(conversation.pinned ? th("unpinnedDone") : th("pinnedDone")) },
      ),
  };
}

/**
 * "Chat options" in the chat header: a disclosure (not an ARIA menu, so Tab and Enter are all
 * it needs) with pin, rename and delete for the open conversation, plus Manage memory and
 * All chats. Escape or a click elsewhere closes it; focus returns to the button.
 */
export function ConversationMenu({ conversation }: { conversation: ConversationSummary | null }) {
  const t = useTranslations("ask");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const actions = useConversationActions(() => router.push("/ask"));

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!panel.current?.contains(target) && !button.current?.contains(target)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      button.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const run = (action: () => void) => {
    setOpen(false);
    action();
  };

  return (
    <div className="relative">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={t("chat.optionsLabel")}
        title={t("chat.optionsLabel")}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex size-9 items-center justify-center rounded-full border border-border-soft text-ink-muted hover:bg-surface-muted hover:text-ink"
      >
        <Icon name="more" className="size-5" />
      </button>
      <div
        ref={panel}
        id={panelId}
        hidden={!open}
        className="chat-pop absolute end-0 top-full z-30 mt-2 w-60 rounded-lg border border-border bg-surface p-1.5 shadow-popover"
      >
        <ul className="space-y-0.5">
          {conversation ? (
            <>
              <Item
                icon="pin"
                label={conversation.pinned ? t("history.unpin") : t("history.pin")}
                onClick={() => run(() => actions.togglePin(conversation))}
              />
              <Item
                icon="pencil"
                label={t("history.rename")}
                onClick={() => run(() => actions.rename(conversation))}
              />
              <Item
                icon="trash"
                label={t("history.delete")}
                danger
                onClick={() => run(() => actions.remove(conversation))}
              />
            </>
          ) : null}
          <li>
            <Link href="/ask/memory" className={itemClasses} onClick={() => setOpen(false)}>
              <Icon name="memory" className="size-4" />
              {t("memory.manage")}
            </Link>
          </li>
          <li>
            <Link href="/ask/history" className={itemClasses} onClick={() => setOpen(false)}>
              <Icon name="clock" className="size-4" />
              {t("chat.allChats")}
            </Link>
          </li>
        </ul>
      </div>
      {actions.dialogs}
    </div>
  );
}
