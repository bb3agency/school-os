import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Icon, type IconName } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";
import { SampleTag } from "./ui";

/**
 * Illustrated product mockups (docs/17 §5.6): HTML, CSS and inline SVG only, so they are crisp
 * at any DPI, need no image requests and carry no style attributes (CSP). Every one is sample
 * data, labelled "Sample data", and hidden from screen readers behind a one-sentence caption.
 * Sample values (not UI text) are kept here; labels come from the `marketing.mock` messages.
 * Every text colour meets AA on its background, like real UI.
 */

const SAMPLE = {
  dobRegister: "14-06-2014",
  dobAadhaar: "16-06-2014",
  dobUdise: "14-06-2014",
  dobBoard: "14-06-2014",
  time1: "10:42",
  time2: "10:15",
  time3: "09:58",
  time4: "09:30",
} as const;

/** A figure whose picture is decorative and whose caption is read instead. */
export function Mock({
  caption,
  children,
  className,
}: {
  caption: string;
  children: ReactNode;
  className?: string | undefined;
}) {
  return (
    <figure className={cn("relative m-0", className)}>
      <figcaption className="sr-only">{caption}</figcaption>
      <div aria-hidden="true" className="select-none">
        {children}
      </div>
    </figure>
  );
}

function Card({ children, className }: { children: ReactNode; className?: string | undefined }) {
  return (
    <div className={cn("rounded-xl border border-border bg-surface p-4 sm:p-5", className)}>
      {children}
    </div>
  );
}

function Chip({
  tone,
  icon,
  children,
}: {
  tone: "ok" | "bad" | "info" | "neutral" | "violet";
  icon?: IconName;
  children: ReactNode;
}) {
  const tones = {
    ok: "bg-positive-soft text-positive-ink",
    bad: "bg-danger-soft text-danger",
    info: "bg-info-soft text-info-ink",
    neutral: "bg-surface-muted text-ink-muted",
    violet: "bg-violet-soft text-violet-ink",
  } as const;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        tones[tone],
      )}
    >
      {icon ? <Icon name={icon} className="size-3" /> : null}
      {children}
    </span>
  );
}

export function FindingCard({
  className,
  flag = false,
}: {
  className?: string | undefined;
  flag?: boolean;
}) {
  const t = useTranslations("marketing.mock.finding");
  const ts = useTranslations("marketing");
  const rows = [
    { label: t("register"), value: SAMPLE.dobRegister, ok: true },
    { label: t("aadhaar"), value: SAMPLE.dobAadhaar, ok: false },
    { label: t("udise"), value: SAMPLE.dobUdise, ok: true },
    { label: t("board"), value: SAMPLE.dobBoard, ok: true },
  ];
  return (
    <Card className={className}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Chip tone="bad" icon="alert">
          {t("badge")}
        </Chip>
        <SampleTag label={ts("sample")} />
      </div>
      <p className="mt-3 font-semibold text-ink">{t("title")}</p>
      <p className="text-sm text-ink-muted">{t("student")}</p>
      <dl className="mt-3 divide-y divide-border overflow-hidden rounded-lg border border-border text-sm">
        {rows.map((row) => (
          <div
            key={row.label}
            className={cn(
              "flex items-center justify-between gap-3 px-3 py-2",
              row.ok ? "bg-surface" : "bg-danger-soft",
              !row.ok && flag && "mk-flag",
            )}
          >
            <dt className="min-w-0 text-ink-muted">{row.label}</dt>
            <dd className="flex shrink-0 items-center gap-2 font-mono text-xs whitespace-nowrap text-ink">
              <span className="max-[380px]:hidden">{row.value}</span>
              {row.ok ? (
                <Chip tone="ok" icon="check">
                  {t("matches")}
                </Chip>
              ) : (
                <Chip tone="bad" icon="alert">
                  {t("differs")}
                </Chip>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 flex gap-2 text-xs text-ink-subtle">
        <Icon name="info" className="mt-px size-3.5" />
        {t("route")}
      </p>
    </Card>
  );
}

export function ImportCard({ className }: { className?: string | undefined }) {
  const t = useTranslations("marketing.mock.import");
  const ts = useTranslations("marketing");
  const steps = ["upload", "preview", "confirm"] as const;
  return (
    <Card className={cn("p-4", className)}>
      <div className="flex items-center gap-2.5">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-success-soft text-success-ink">
          <Icon name="file" className="size-4" />
        </span>
        <p className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">{t("title")}</p>
        <SampleTag label={ts("sample")} />
      </div>
      <ol className="mt-3 flex items-center gap-1.5">
        {steps.map((step, index) => (
          <li key={step} className="flex min-w-0 flex-1 items-center gap-1.5">
            <span
              className={cn(
                "flex size-5 shrink-0 items-center justify-center rounded-full text-[0.625rem] font-semibold",
                index < 2 ? "bg-action text-on-action" : "border border-border-control text-ink",
              )}
            >
              {index < 1 ? <Icon name="check" className="size-3" /> : index + 1}
            </span>
            <span
              className={cn(
                "truncate text-xs",
                index === 1 ? "font-semibold text-ink" : "text-ink-muted",
              )}
            >
              {t(`steps.${step}`)}
            </span>
          </li>
        ))}
      </ol>
      <p className="mt-3 text-xs text-ink-muted">{t("rows")}</p>
      <p className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-surface-muted px-2 py-0.5 text-xs font-medium text-ink">
        <Icon name="refresh" className="size-3" />
        {t("undo")}
      </p>
    </Card>
  );
}

export function AskCard({
  className,
  notFound = false,
}: {
  className?: string | undefined;
  notFound?: boolean;
}) {
  const t = useTranslations("marketing.mock.ask");
  return (
    <div
      className={cn(
        "ai-gradient ai-chrome rounded-xl border border-transparent p-4 text-white sm:p-5",
        className,
      )}
    >
      <p className="eyebrow flex items-center gap-1.5 text-white">
        <Icon name="sparkles" className="size-4" />
        {t("label")}
      </p>
      <p className="mt-3 ms-auto w-fit max-w-[90%] rounded-lg rounded-br-sm bg-surface px-3 py-2 text-sm text-ink">
        {t("question")}
      </p>
      <p className="mt-3 text-sm leading-relaxed text-white">
        {t("answer")}
        <sup className="ms-0.5 font-mono text-[0.6875rem]">[1]</sup>
        <sup className="font-mono text-[0.6875rem]">[2]</sup>
      </p>
      <p className="eyebrow mt-3 text-white">{t("sources")}</p>
      <ul className="mt-1.5 flex flex-wrap gap-1.5">
        {[t("sourceRegister"), t("sourceRecord")].map((source, index) => (
          <li
            key={source}
            className="inline-flex items-center gap-1 rounded-full bg-surface px-2.5 py-0.5 text-xs font-medium text-ink"
          >
            <span className="font-mono text-primary">[{index + 1}]</span>
            {source}
          </li>
        ))}
      </ul>
      {notFound ? (
        <p className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-white/40 px-2.5 py-0.5 text-xs text-white">
          <Icon name="search" className="size-3" />
          {t("notFound")}
        </p>
      ) : null}
    </div>
  );
}

export function ChangeRequestCard({ className }: { className?: string | undefined }) {
  const t = useTranslations("marketing.mock.change");
  const ts = useTranslations("marketing");
  return (
    <Card className={className}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-ink">{t("title")}</p>
        <SampleTag label={ts("sample")} />
      </div>
      <p className="mt-1 text-sm text-ink-muted">{t("field")}</p>
      <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
        <div className="rounded-lg border border-border bg-surface-muted p-3">
          <p className="eyebrow text-ink-muted">{t("old")}</p>
          <p className="mt-1 font-mono text-ink line-through decoration-danger/70">
            {SAMPLE.dobAadhaar}
          </p>
        </div>
        <div className="rounded-lg border border-positive-border bg-positive-soft p-3">
          <p className="eyebrow text-positive-ink">{t("new")}</p>
          <p className="mt-1 font-mono text-ink">{SAMPLE.dobRegister}</p>
        </div>
      </div>
      <p className="mt-3 flex items-center gap-2 text-sm text-ink">
        <Icon name="file" className="size-4 text-ink-muted" />
        {t("evidence")}
      </p>
      <ol className="mt-4 space-y-2 border-t border-border pt-4 text-sm">
        <li className="flex items-center gap-2 text-ink">
          <span className="flex size-5 items-center justify-center rounded-full bg-success text-white">
            <Icon name="check" className="size-3" />
          </span>
          {t("requested")}
        </li>
        <li className="flex items-center gap-2 text-ink">
          <span className="size-5 rounded-full border-2 border-dashed border-border-control" />
          {t("approve")}
        </li>
      </ol>
      <p className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-violet-soft px-2.5 py-0.5 text-xs font-medium text-violet-ink">
        <Icon name="users" className="size-3" />
        {t("rule")}
      </p>
    </Card>
  );
}

export function AuditCard({ className }: { className?: string | undefined }) {
  const t = useTranslations("marketing.mock.audit");
  const ts = useTranslations("marketing");
  const events = [
    { label: t("e1"), time: SAMPLE.time1, icon: "clipboard" },
    { label: t("e2"), time: SAMPLE.time2, icon: "upload" },
    { label: t("e3"), time: SAMPLE.time3, icon: "sparkles" },
    { label: t("e4"), time: SAMPLE.time4, icon: "key" },
  ] as const;
  return (
    <Card className={className}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-ink">{t("title")}</p>
        <SampleTag label={ts("sample")} />
      </div>
      <ol className="relative mt-4 space-y-3 before:absolute before:inset-y-2 before:start-[0.8125rem] before:w-px before:bg-border">
        {events.map((event) => (
          <li key={event.label} className="relative flex items-center gap-3 text-sm">
            <span className="z-10 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-surface text-ink-muted">
              <Icon name={event.icon} className="size-3.5" />
            </span>
            <span className="min-w-0 flex-1 text-ink">{event.label}</span>
            <span className="font-mono text-xs text-ink-subtle">{event.time}</span>
          </li>
        ))}
      </ol>
      <p className="mt-4 flex items-center gap-2 rounded-lg bg-success-soft px-3 py-2 text-sm font-medium text-success-ink">
        <Icon name="shieldCheck" className="size-4" />
        {t("check")}
      </p>
    </Card>
  );
}

export function CertificateCard({ className }: { className?: string | undefined }) {
  const t = useTranslations("marketing.mock.certificate");
  const ts = useTranslations("marketing");
  return (
    <Card className={cn("relative overflow-hidden", className)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Chip tone="info" icon="clock">
          {ts("planned")}
        </Chip>
        <SampleTag label={ts("sample")} />
      </div>
      <div className="mt-4 rounded-lg border border-border-soft bg-surface p-4 shadow-raised">
        <div className="mx-auto h-2 w-24 rounded-full bg-surface-sunken" />
        <p className="mt-3 text-center font-display text-2xl text-ink">{t("title")}</p>
        <p className="text-center font-mono text-xs text-ink-muted">{t("serial")}</p>
        <dl className="mt-4 space-y-2 text-sm">
          {[t("name"), t("class")].map((label) => (
            <div key={label} className="flex items-end gap-2">
              <dt className="shrink-0 text-ink-muted">{label}</dt>
              <dd className="h-3 flex-1 border-b border-dashed border-border-control" />
            </div>
          ))}
        </dl>
      </div>
      <p className="mt-3 text-xs text-ink-subtle">{t("draft")}</p>
    </Card>
  );
}

export function AccessRequestCard({ className }: { className?: string | undefined }) {
  const t = useTranslations("marketing.mock.access");
  const ts = useTranslations("marketing");
  return (
    <Card className={className}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex items-center gap-2 font-semibold text-ink">
          <Icon name="key" className="size-4 text-ink-muted" />
          {t("title")}
        </p>
        <SampleTag label={ts("sample")} />
      </div>
      <ul className="mt-3 space-y-1.5 text-sm text-ink-muted">
        <li>{t("reason")}</li>
        <li>{t("scope")}</li>
        <li className="flex items-center gap-1.5">
          <Icon name="clock" className="size-3.5" />
          {t("time")}
        </li>
      </ul>
      <div className="mt-4 flex gap-2">
        <span className="inline-flex min-h-9 flex-1 items-center justify-center rounded-md bg-action px-3 text-sm font-medium text-on-action">
          {t("approve")}
        </span>
        <span className="inline-flex min-h-9 flex-1 items-center justify-center rounded-md border border-border-soft px-3 text-sm font-medium text-ink">
          {t("decline")}
        </span>
      </div>
    </Card>
  );
}

/** Mumbai → Hyderabad: where records live and where backups go. Inline SVG, decorative. */
export function RegionDiagram({
  primary,
  primaryNote,
  backup,
  backupNote,
}: {
  primary: string;
  primaryNote: string;
  backup: string;
  backupNote: string;
}) {
  return (
    <div className="relative rounded-2xl border border-border bg-surface p-6 sm:p-8">
      <svg viewBox="0 0 400 160" className="h-auto w-full" fill="none" aria-hidden="true">
        <defs>
          <linearGradient id="mk-route" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0" stopColor="#2563eb" />
            <stop offset="1" stopColor="#0d9488" />
          </linearGradient>
        </defs>
        <path
          d="M70 110 C 150 20, 250 20, 330 90"
          stroke="url(#mk-route)"
          strokeWidth="2.5"
          strokeDasharray="6 7"
          strokeLinecap="round"
        />
        <circle cx="70" cy="110" r="26" fill="#e8efff" />
        <circle cx="70" cy="110" r="9" fill="#1d4ed8" />
        <circle cx="330" cy="90" r="26" fill="#e6f7f5" />
        <circle cx="330" cy="90" r="9" fill="#0f766e" />
        <path
          d="M196 38 l8 5 -8 5"
          stroke="#0f766e"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <div className="mt-2 grid grid-cols-2 gap-4 text-sm">
        <div>
          <p className="font-semibold text-ink">{primary}</p>
          <p className="text-ink-muted">{primaryNote}</p>
        </div>
        <div className="text-end">
          <p className="font-semibold text-ink">{backup}</p>
          <p className="text-ink-muted">{backupNote}</p>
        </div>
      </div>
    </div>
  );
}

/**
 * Hero composition: a product window with a findings list and the open mismatch, an import
 * waiting for confirmation floating above it, and an Ask answer with sources below. The
 * layers settle in once (CSS, marketing.css `.mk-settle`), 120ms apart.
 */
export function HeroComposition() {
  const t = useTranslations("marketing.mock");
  const nav: IconName[] = ["home", "users", "shieldCheck", "clipboard", "sparkles", "activity"];
  return (
    <Mock caption={t("hero")} className="mx-auto w-full max-w-xl lg:max-w-none">
      <div className="relative pt-2 sm:pt-16 lg:ps-4">
        <div className="mk-window mk-settle overflow-hidden rounded-2xl">
          <div className="flex items-center gap-2 border-b border-border bg-surface-muted px-4 py-2.5">
            <span className="flex gap-1.5">
              <span className="size-2.5 rounded-full bg-border-soft" />
              <span className="size-2.5 rounded-full bg-border-soft" />
              <span className="size-2.5 rounded-full bg-border-soft" />
            </span>
            <span className="mx-auto font-mono text-xs text-ink-muted">{t("window")}</span>
          </div>
          <div className="flex">
            <div className="hidden w-14 shrink-0 flex-col items-center gap-3 border-e border-border bg-surface py-4 sm:flex">
              <span className="mb-1 flex size-7 items-center justify-center rounded-full bg-action font-display text-sm text-on-action">
                S
              </span>
              {nav.map((icon, index) => (
                <span
                  key={icon}
                  className={cn(
                    "flex size-8 items-center justify-center rounded-lg",
                    index === 2 ? "bg-primary-soft text-primary" : "text-ink-subtle",
                  )}
                >
                  <Icon name={icon} className="size-4" />
                </span>
              ))}
            </div>
            <div className="min-w-0 flex-1 bg-canvas p-3 sm:p-5">
              <FindingCard flag className="shadow-card" />
              <div className="mt-3 flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3 sm:mb-16">
                <Chip tone="neutral" icon="info">
                  {t("finding.missing")}
                </Chip>
                <p className="min-w-0 truncate text-sm text-ink">
                  <span className="font-semibold">{t("finding.missingTitle")}</span>
                  <span className="text-ink-muted"> · {t("finding.missingStudent")}</span>
                </p>
              </div>
            </div>
          </div>
        </div>
        <ImportCard className="mk-float mk-settle mk-settle-2 absolute top-0 right-2 hidden w-72 sm:block lg:-right-6" />
        <AskCard className="mk-float mk-settle mk-settle-3 relative mt-4 sm:-mt-14 sm:ms-6 sm:w-80 lg:-ms-4" />
      </div>
    </Mock>
  );
}
