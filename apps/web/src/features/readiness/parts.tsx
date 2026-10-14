import { useTranslations } from "next-intl";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Icon, type IconName } from "@/components/ui/Icon";
import type { DiffSegment, FixOwner, ReadinessStatus } from "./types";

export const statusTone: Record<ReadinessStatus, BadgeTone> = {
  ready: "success",
  needs_parent: "info",
  needs_school: "warning",
  blocked: "danger",
};

const statusIcon: Record<ReadinessStatus, IconName> = {
  ready: "checkCircle",
  needs_parent: "users",
  needs_school: "building",
  blocked: "alert",
};

/** Readiness of a student; an icon per status so colour is never the only signal. */
export function ReadinessBadge({ status }: { status: ReadinessStatus }) {
  const t = useTranslations("readiness.status");
  return (
    <Badge tone={statusTone[status]}>
      <Icon name={statusIcon[status]} className="size-3.5" />
      {t(status)}
    </Badge>
  );
}

const ownerTone: Record<FixOwner, BadgeTone> = {
  parent_aadhaar: "info",
  school_udise: "warning",
  school_register: "violet",
  unknown: "danger",
};

const ownerIcon: Record<FixOwner, IconName> = {
  parent_aadhaar: "users",
  school_udise: "building",
  school_register: "clipboard",
  unknown: "alert",
};

/** Who must fix a difference: the parent (Aadhaar), the school (UDISE+ or the register). */
export function OwnerBadge({ owner }: { owner: FixOwner }) {
  const t = useTranslations("readiness.owner");
  return (
    <Badge tone={ownerTone[owner]}>
      <Icon name={ownerIcon[owner]} className="size-3.5" />
      {t(owner)}
    </Badge>
  );
}

/** Shows spaces in a changed piece, so "SAI KUMAR" vs "SAIKUMAR" is visible. */
function visible(text: string): string {
  return text.replace(/ /g, "␣");
}

/**
 * The exact difference, character by character: what the right value has and the other
 * record lacks is struck through (<del>), what the other record adds is underlined (<ins>).
 * Screen readers hear "removed" / "added" before each changed piece.
 */
export function DiffView({
  segments,
  referenceLabel,
  otherLabel,
}: {
  segments: readonly DiffSegment[];
  referenceLabel: string;
  otherLabel: string;
}) {
  const t = useTranslations("readiness.diff");
  return (
    <div className="space-y-1 font-mono text-sm">
      <p>
        <span className="mr-2 font-sans text-xs font-semibold text-ink-muted">
          {referenceLabel}
        </span>
        {segments.map((segment, index) =>
          segment.op === "equal" ? (
            <span key={index}>{segment.reference}</span>
          ) : segment.reference ? (
            <del
              key={index}
              className="rounded-sm bg-success-soft px-0.5 text-success-ink decoration-2"
            >
              <span className="sr-only">{t("missingThere")} </span>
              {visible(segment.reference)}
            </del>
          ) : null,
        )}
      </p>
      <p>
        <span className="mr-2 font-sans text-xs font-semibold text-ink-muted">{otherLabel}</span>
        {segments.map((segment, index) =>
          segment.op === "equal" ? (
            <span key={index}>{segment.other}</span>
          ) : segment.other ? (
            <ins
              key={index}
              className="rounded-sm bg-danger-soft px-0.5 text-danger underline decoration-2"
            >
              <span className="sr-only">{t("differsHere")} </span>
              {visible(segment.other)}
            </ins>
          ) : (
            <ins
              key={index}
              className="rounded-sm bg-danger-soft px-0.5 text-danger no-underline"
              aria-label={t("nothingHere")}
            >
              ‸
            </ins>
          ),
        )}
      </p>
    </div>
  );
}

/** "142 of 160 ready" with a native progress bar (CSP-safe: no inline style). */
export function ReadyBar({
  ready,
  students,
  label,
}: {
  ready: number;
  students: number;
  label: string;
}) {
  return (
    <div className="min-w-40 space-y-1">
      <span className="block text-sm tabular-nums">{label}</span>
      <progress
        max={Math.max(students, 1)}
        value={ready}
        aria-label={label}
        className="h-2 w-full accent-primary"
      />
    </div>
  );
}
