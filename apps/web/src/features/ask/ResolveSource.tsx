"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import * as z from "@/lib/zod";
import { useRouter } from "@/i18n/navigation";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { isSafeAppPath } from "./answer";

/**
 * `GET /documents/{id}/pages/{n}` (D73). Local schema until the generated client carries the
 * route (the lead regenerates apps/api/openapi.json and packages/api-client).
 */
export const pageLinkSchema = z.object({
  url: z.string().min(1),
  page: z.number().int().min(1),
  expires_at: z.string(),
  content_type: z.string(),
  version_no: z.number().int().min(1),
});
export type PageLink = z.infer<typeof pageLinkSchema>;

type PagePath = "/api/v1/documents/{document_id}/pages/{n}";
type PageClient = {
  GET: (
    path: PagePath,
    init: {
      params: { path: { document_id: string; n: number }; query?: { version?: number } };
    },
  ) => Promise<{ data?: unknown; error?: unknown; response: Response }>;
};

/**
 * Where the browser opens a cited page: the presigned link (http/https only) with
 * `#page=n` for a PDF (the browser's viewer jumps to it); null for anything else.
 */
export function citedPageUrl(link: PageLink, page: number): string | null {
  let url: URL;
  try {
    url = new URL(link.url);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  url.hash = link.content_type === "application/pdf" ? `page=${page}` : "";
  return url.toString();
}

/** Tests replace the opener (jsdom has no new tabs). */
type Opener = (url: string) => void;
let openerOverride: Opener | undefined;
export function setPageOpenerForTesting(opener: Opener | undefined): void {
  openerOverride = opener;
}
function openInNewTab(url: string): void {
  if (openerOverride) {
    openerOverride(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

/**
 * The cited page of a document source (D73): the "page n" chip is a button that asks the API
 * for a short-lived link to the cited version (same checks as a download) and opens it in a
 * new tab at that page. Any error (no longer visible, not scanned yet, a file the browser
 * cannot show) falls back to what the source title does: open the document's own screen.
 */
export function CitedPageButton({
  documentId,
  version,
  page,
  name,
}: {
  documentId: string;
  version: number;
  page: number;
  name: string;
}) {
  const t = useTranslations("ask.answer");
  const api = useBffClient("staff") as unknown as PageClient;
  const router = useRouter();
  const [opening, setOpening] = useState(false);

  const open = async () => {
    setOpening(true);
    try {
      const link = pageLinkSchema.parse(
        await unwrap(
          api.GET("/api/v1/documents/{document_id}/pages/{n}", {
            params: { path: { document_id: documentId, n: page }, query: { version } },
          }),
        ),
      );
      const target = citedPageUrl(link, page);
      if (!target) throw new Error("unsafe page link");
      openInNewTab(target);
    } catch {
      router.push(`/documents/${documentId}`);
    } finally {
      setOpening(false);
    }
  };

  return (
    <button
      type="button"
      onClick={() => void open()}
      disabled={opening}
      aria-label={t("openPage", { page, title: name })}
      className="inline-flex min-h-6 items-center gap-1 rounded-full border border-border bg-surface-muted px-2.5 py-0.5 text-xs font-semibold text-ink-muted underline-offset-4 hover:border-accent hover:bg-accent-soft hover:text-accent-strong hover:underline disabled:opacity-60 motion-safe:transition-colors"
    >
      {t("page", { page })}
    </button>
  );
}

type Outcome = "idle" | "opening" | "gone" | "noScreen" | "failed";

/**
 * A `sos://` source this page does not know how to open (a kind added after this build):
 * asks `GET /knowledge/resolve` (FR-KB-042, US-2503), then opens the screen it names. A source
 * the member can no longer open answers 404 and the chip says so ("Source you can no longer
 * open"); the API names only an app-relative path, never a URL.
 */
export function ResolveSourceLink({ source, name }: { source: string; name: string }) {
  const t = useTranslations("ask.answer");
  const api = useBffClient("staff");
  const router = useRouter();
  const [outcome, setOutcome] = useState<Outcome>("idle");

  const open = async () => {
    setOutcome("opening");
    try {
      const target = await unwrap(
        api.GET("/api/v1/knowledge/resolve", { params: { query: { source } } }),
      );
      if (target.href && isSafeAppPath(target.href)) {
        setOutcome("idle");
        router.push(target.href);
      } else {
        setOutcome("noScreen");
      }
    } catch (error) {
      setOutcome(error instanceof ApiError && error.status === 404 ? "gone" : "failed");
    }
  };

  if (outcome === "gone") {
    return <span className="text-xs text-ink-muted">{t("resolveGone")}</span>;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2">
      <button
        type="button"
        onClick={() => void open()}
        disabled={outcome === "opening"}
        aria-label={`${name} (${t("resolveOpen")})`}
        className="min-h-6 font-semibold text-primary underline underline-offset-4 break-anywhere hover:no-underline disabled:opacity-60"
      >
        {name}
      </button>
      {outcome === "noScreen" ? (
        <span className="text-xs text-ink-muted">{t("resolveNoScreen")}</span>
      ) : null}
      {outcome === "failed" ? (
        <span role="alert" className="text-xs text-danger">
          {t("resolveFailed")}
        </span>
      ) : null}
    </span>
  );
}
