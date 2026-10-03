"use client";

import { useTranslations } from "next-intl";
import { Icon } from "@/components/ui/Icon";
import type { AskState, AskStepRecord } from "./answer";

type StepsT = ReturnType<typeof useTranslations<"ask.steps">>;

/** The label of one step ("Searching school documents… 4 found"). */
export function stepLabel(record: AskStepRecord, t: StepsT, withCount = true): string {
  if (record.step === "searching_documents" && withCount && record.count !== null) {
    return t("searching_documents_count", { count: record.count });
  }
  return t(record.step);
}

/** What the live status line says now (also what the status region announces, without counts). */
export function currentStep(state: AskState): AskStepRecord | null {
  const last = state.steps[state.steps.length - 1];
  if (last) return last;
  if (state.phase === "waiting") return { step: "understanding", count: null };
  if (state.phase === "streaming") return { step: "writing", count: null };
  return null;
}

/**
 * The live status line while an answer is being prepared: the current step with a soft moving
 * light (still under reduced motion). Visual only; the chat's status region announces steps.
 */
export function LiveStatus({ state }: { state: AskState }) {
  const t = useTranslations("ask.steps");
  const step = currentStep(state);
  if (!step) return null;
  return (
    <p aria-hidden="true" className="chat-fade flex min-h-6 items-center gap-2 text-sm">
      <span className="relative flex size-2.5 shrink-0" aria-hidden="true">
        <span className="absolute inset-0 rounded-full bg-primary opacity-30 motion-safe:animate-ping" />
        <span className="relative size-2.5 rounded-full bg-primary" />
      </span>
      <span key={step.step} className="chat-shimmer-text chat-fade">
        {stepLabel(step, t)}
      </span>
    </p>
  );
}

/**
 * After an answer: "Worked for 4 seconds · 3 sources", a native disclosure listing the steps
 * the server reported. Shown only when the stream reported steps or its time.
 */
export function WorkedFor({ state }: { state: AskState }) {
  const t = useTranslations("ask.steps");
  if (state.steps.length === 0 && state.latencyMs === null) return null;
  const seconds = Math.max(1, Math.round((state.latencyMs ?? 0) / 1000));
  const summary = [
    state.latencyMs !== null ? t("workedFor", { seconds }) : null,
    t("workedSources", { count: state.citations.length }),
  ]
    .filter(Boolean)
    .join(" · ");
  if (state.steps.length === 0) {
    return <p className="text-sm text-ink-subtle">{summary}</p>;
  }
  return (
    <details className="group text-sm text-ink-subtle">
      <summary className="inline-flex min-h-6 cursor-pointer list-none items-center gap-1 rounded-md hover:text-ink [&::-webkit-details-marker]:hidden">
        {summary}
        <Icon
          name="chevronDown"
          className="size-4 motion-safe:transition-transform group-open:rotate-180"
        />
      </summary>
      <ol aria-label={t("listLabel")} className="mt-2 space-y-1 border-s border-border ps-3">
        {state.steps.map((record, i) => (
          <li key={`${record.step}-${i}`} className="flex items-center gap-2">
            <Icon name="check" className="size-3.5 text-success" />
            <span>
              {t(record.step)}
              {record.count !== null ? ` ${t("stepFound", { count: record.count })}` : ""}
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}
