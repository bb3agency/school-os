"use client";

import { INITIAL_ASK, type AskState } from "./answer";
import { LiveStatus } from "./Status";
import { ChatTurn, type TurnHandlers, type TurnModel } from "./Turn";

/*
 * Synthetic samples for the dev UI reference (invariant 11: no real data): one finished
 * turn and one still streaming, drawn with the real chat components (docs/17 §5.3).
 */
const DONE: AskState = {
  ...INITIAL_ASK,
  phase: "done",
  queryId: "0192f3a4-0000-7000-8000-0000000de001",
  language: "en",
  mode: "full",
  status: "answered",
  finalized: true,
  text: "Half-yearly exams begin on **22/09/2026**. [1]\n\n| Class | Starts |\n|---|---|\n| 6 | 9 am |\n| 7 | 10 am |",
  citations: [
    {
      index: 1,
      source: "sos://doc/0192f3a4-0000-7000-8000-0000000de101/v2#p1",
      title: "Sample circular · Exam timings",
      snippet: "Half-yearly exams begin on 22/09/2026 at 9 am for Class 6.",
    },
  ],
  steps: [
    { step: "understanding", count: null },
    { step: "searching_documents", count: 3 },
    { step: "writing", count: null },
  ],
  latencyMs: 4200,
  followups: ["When do Class 8 exams begin?", "Is there a timetable?"],
};

const STREAMING: AskState = {
  ...INITIAL_ASK,
  phase: "streaming",
  steps: [{ step: "searching_documents", count: 4 }],
};

const HANDLERS: TurnHandlers = {
  busy: false,
  canVerify: false,
  onVersion: () => undefined,
  onRegenerate: () => undefined,
  onEdit: () => null,
  onRetry: () => undefined,
  onFollowUp: () => undefined,
};

const TURN: TurnModel = {
  key: "sample",
  question: "When do the half-yearly exams begin?",
  state: DONE,
  live: false,
  latest: true,
  feedback: null,
  versions: { index: 1, total: 2, group: "sample" },
  past: false,
};

export function ChatPreview() {
  return (
    <div className="space-y-6">
      <ChatTurn turn={TURN} handlers={HANDLERS} />
      <LiveStatus state={STREAMING} />
    </div>
  );
}
