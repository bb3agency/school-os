"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Link } from "@/i18n/navigation";
import type { MemoryEvent } from "./contract";
import { useConfirmMemory, useDeleteMemory } from "./memory";

/** "Remember this? '…'" with Save (confirm) and Dismiss (delete), under the answer. */
function Suggestion({ item }: { item: MemoryEvent }) {
  const t = useTranslations("ask.memory");
  const confirm = useConfirmMemory();
  const dismiss = useDeleteMemory();
  const [done, setDone] = useState<"saved" | "dismissed" | null>(null);
  const failed = confirm.error ?? dismiss.error;

  if (done) {
    return (
      <p role="status" className="chat-fade flex items-center gap-2 text-sm text-ink-muted">
        <Icon name={done === "saved" ? "checkCircle" : "close"} className="size-4" />
        {done === "saved" ? t("suggestSaved") : t("suggestDismissed")}
        {done === "saved" ? (
          <Link
            href="/ask/memory"
            className="font-medium text-primary underline underline-offset-4"
          >
            {t("manage")}
          </Link>
        ) : null}
      </p>
    );
  }

  const pending = confirm.isPending || dismiss.isPending;
  return (
    <div
      role="group"
      aria-label={t("suggestTitle")}
      className="chat-enter space-y-2 rounded-lg border border-violet-soft bg-violet-soft p-3 text-sm"
    >
      <p className="flex items-center gap-2 font-medium text-violet-ink">
        <Icon name="memory" className="size-4" />
        {t("suggestTitle")}
      </p>
      <p className="text-ink break-anywhere">“{item.text}”</p>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={pending}
          aria-label={t("confirmFor", { text: item.text })}
          onClick={() => confirm.mutate(item.item_id, { onSuccess: () => setDone("saved") })}
        >
          {t("confirm")}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={pending}
          aria-label={t("dismissFor", { text: item.text })}
          onClick={() => dismiss.mutate(item.item_id, { onSuccess: () => setDone("dismissed") })}
        >
          {t("dismiss")}
        </Button>
      </div>
      <ApiErrorAlert error={failed ?? undefined} namespace="ask" />
    </div>
  );
}

/**
 * What the server did with memory while answering: a quiet "Memory updated" note with a link
 * to Manage memory, or a suggestion to confirm or dismiss (nothing is remembered from a
 * suggestion until the member saves it: LLM writes need a human, invariant 9).
 */
export function MemoryNotes({ memory }: { memory: readonly MemoryEvent[] }) {
  const t = useTranslations("ask.memory");
  if (memory.length === 0) return null;
  const saved = memory.some((item) => item.action === "saved");
  const suggestions = memory.filter((item) => item.action === "suggested");
  return (
    <div className="space-y-2">
      {saved ? (
        <p className="chat-enter flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-muted">
          <Icon name="memory" className="size-4 text-violet-ink" />
          <span>{t("updated")}</span>
          <span aria-hidden="true">·</span>
          <Link
            href="/ask/memory"
            className="font-medium text-primary underline underline-offset-4"
          >
            {t("manage")}
          </Link>
        </p>
      ) : null}
      {suggestions.map((item) => (
        <Suggestion key={item.item_id} item={item} />
      ))}
    </div>
  );
}
