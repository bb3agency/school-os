"use client";

import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { Input } from "./Input";
import { Label } from "./Label";

/**
 * A secret the API returns exactly once (dedicated heartbeat key, docs/16 §5.4, §12.2).
 * It lives only in this component's memory: never logged, never stored in the browser.
 * The operator copies it (or selects it), then confirms they stored it before continuing.
 */
export function SecretOnce({
  label,
  secret,
  keyId,
  onDone,
  doneLabel,
}: {
  label: string;
  secret: string;
  keyId?: string | null;
  onDone: () => void;
  doneLabel: string;
}) {
  const t = useTranslations("common.secret");
  const inputId = useId();
  const confirmId = useId();
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  const [stored, setStored] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied("copied");
    } catch {
      setCopied("failed");
    }
  }

  return (
    <div className="space-y-4">
      <Alert tone="warning" title={t("onceTitle")}>
        {t("onceBody")}
      </Alert>
      {keyId ? (
        <p className="text-sm">
          {t("keyId")} <code className="font-mono">{keyId}</code>
        </p>
      ) : null}
      <div className="space-y-1">
        <Label htmlFor={inputId}>{label}</Label>
        <div className="flex gap-2">
          <Input
            id={inputId}
            readOnly
            value={secret}
            spellCheck={false}
            autoComplete="off"
            className="font-mono text-sm"
            onFocus={(event) => event.currentTarget.select()}
          />
          <Button variant="secondary" onClick={copy}>
            {t("copy")}
          </Button>
        </div>
        <p role="status" className="text-sm text-ink-muted">
          {copied === "copied" ? t("copied") : copied === "failed" ? t("copyFailed") : ""}
        </p>
      </div>
      <div className="flex items-start gap-2">
        <input
          id={confirmId}
          type="checkbox"
          checked={stored}
          onChange={(event) => setStored(event.currentTarget.checked)}
          className="mt-1 size-4 accent-primary"
        />
        <label htmlFor={confirmId} className="text-sm">
          {t("stored")}
        </label>
      </div>
      <div className="flex justify-end">
        <Button onClick={onDone} disabled={!stored}>
          {doneLabel}
        </Button>
      </div>
    </div>
  );
}
