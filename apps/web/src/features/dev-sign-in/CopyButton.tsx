"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Button } from "@/components/ui/Button";

type CopyState = "idle" | "copied" | "failed";

async function writeClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/**
 * Copies `text` (or, with `stepUpClaims`, a fresh `{"auth_time": <now>}` for the stub's claims
 * box, built at click time) and announces the result in a polite live region.
 */
export function CopyButton({
  text,
  label,
  stepUpClaims = false,
}: {
  text?: string;
  label: string;
  stepUpClaims?: boolean;
}) {
  const t = useTranslations("devSignIn");
  const [state, setState] = useState<CopyState>("idle");

  async function copy() {
    const value = stepUpClaims
      ? JSON.stringify({ auth_time: Math.floor(Date.now() / 1000) })
      : (text ?? "");
    setState((await writeClipboard(value)) ? "copied" : "failed");
  }

  return (
    <span className="inline-flex items-center gap-2">
      <Button variant="secondary" size="sm" onClick={() => void copy()}>
        {label}
      </Button>
      <span role="status" className="text-xs text-ink-muted">
        {state === "copied" ? t("copied") : state === "failed" ? t("copyFailed") : ""}
      </span>
    </span>
  );
}
