"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { LoadingState } from "@/components/ui/LoadingState";
import type { Loadable } from "@/lib/loadable";
import type { DueState, ReadingStatus, TaskStatus } from "./data";

const readingPill: Record<ReadingStatus, PillVariant> = {
  not_read: "tag",
  queued: "progress",
  running: "progress",
  ready: "done",
  needs_review: "review",
};

export function ReadingPill({ status }: { status: ReadingStatus }) {
  const t = useTranslations("circulars.status");
  return <Pill variant={readingPill[status]}>{t(status)}</Pill>;
}

const taskPill: Record<TaskStatus, PillVariant> = {
  open: "tag",
  in_progress: "progress",
  done: "done",
  cancelled: "sample",
};

export function TaskStatusPill({ status }: { status: TaskStatus }) {
  const t = useTranslations("tasks.status");
  return <Pill variant={taskPill[status]}>{t(status)}</Pill>;
}

const duePill: Record<Exclude<DueState, "later" | "closed">, PillVariant> = {
  overdue: "negative",
  today: "review",
  soon: "date",
};

/** "Overdue" / "Due today" / "Due soon" (colour is never the only signal: the words say it). */
export function DuePill({ state }: { state: DueState }) {
  const t = useTranslations("tasks.due");
  if (state === "later" || state === "closed") return null;
  return <Pill variant={duePill[state]}>{t(state)}</Pill>;
}

/** Marks AI output: it is a suggestion to check, never a decision (invariant 9). */
export function AiNote({ children }: { children?: ReactNode }) {
  const t = useTranslations("circulars");
  return (
    <p className="flex flex-wrap items-center gap-2 text-sm text-ink-muted">
      <Pill variant="review">{t("aiBadge")}</Pill>
      <span>{children ?? t("aiNote")}</span>
    </p>
  );
}

/** Loading, not available, error or the content of one query. */
export function LoadGate<T>({
  data,
  children,
}: {
  data: Loadable<T>;
  children: (value: T) => ReactNode;
}) {
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  if (data.status === "loading") return <LoadingState label={tc("loading")} />;
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
