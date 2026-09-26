import type { SupportTicket } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";

/**
 * Messages of a ticket, oldest first. Text is shown as plain text (React escapes it; no
 * HTML rendering). Internal notes only ever reach operators (the school API omits them).
 */
export function TicketThread({ ticket }: { ticket: SupportTicket }) {
  const t = useTranslations("support");
  const messages = ticket.messages ?? [];
  if (messages.length === 0) return <p className="text-sm text-ink-muted">{t("noMessages")}</p>;
  return (
    <ol className="space-y-3" aria-label={t("threadLabel")}>
      {messages.map((message) => {
        const author =
          message.author_type === "operator"
            ? t("authorOperator")
            : message.author_type === "system"
              ? t("authorSystem")
              : t("authorSchool");
        return (
          <li
            key={message.id}
            className={cn(
              "rounded-md border p-3",
              message.internal_note ? "border-warning-border bg-warning-soft" : "border-border",
            )}
          >
            <p className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
              <span className="font-semibold text-ink">{author}</span>
              <span>{formatDateTime(message.created_at)}</span>
              {message.internal_note ? <Badge tone="warning">{t("internalNote")}</Badge> : null}
            </p>
            <p className="mt-2 text-sm whitespace-pre-wrap">{message.body}</p>
          </li>
        );
      })}
    </ol>
  );
}
