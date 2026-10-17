"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { useRouter } from "@/i18n/navigation";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { isSafeAppPath } from "./answer";

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
