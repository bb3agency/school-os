import type { SupportTicket } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";

/**
 * Messages of a ticket, oldest first. Text is shown as plain text (React escapes it; no
 * HTML rendering). Internal notes only ever reach operators (the school API omits them).
 */
export function TicketThread({
  ticket,
  schoolSide = false,
}: {
  ticket: SupportTicket;
  /** The school's view: its own messages sit on the right, SchoolOS on the left. */
  schoolSide?: boolean;
}) {
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
        const own =
          schoolSide && message.author_type !== "operator" && message.author_type !== "system";
        return (
          <li key={message.id} className={cn("flex", own ? "justify-end" : "justify-start")}>
            <div
              className={cn(
                "w-full max-w-2xl rounded-lg border p-4",
                message.internal_note
                  ? "border-warning-border bg-warning-soft"
                  : own
                    ? "border-border bg-surface-sunken"
                    : "border-border bg-surface",
              )}
            >
              <p className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
                <Avatar name={author} size="sm" decorative />
                <span className="font-medium text-ink">{author}</span>
                <span className="font-mono">{formatDateTime(message.created_at)}</span>
                {message.internal_note ? <Badge tone="warning">{t("internalNote")}</Badge> : null}
              </p>
              <p className="mt-2 text-sm whitespace-pre-wrap text-ink">{message.body}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
