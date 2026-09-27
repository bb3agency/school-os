"use client";

import { useTranslations } from "next-intl";
import { useState, type ClipboardEvent, type ComponentProps, type ReactNode } from "react";
import { TextField } from "@/components/ui/Input";
import { containsFullAadhaar } from "./aadhaar";

export interface GuardedTextFieldProps extends Omit<
  ComponentProps<typeof TextField>,
  "error" | "onPaste"
> {
  error?: ReactNode;
}

/**
 * A text field that refuses a pasted full Aadhaar number and explains why (US-303 AC1,
 * FR-STU-012). Typing one shows the same explanation; the API refuses it anyway (422
 * `aadhaar_full_number_rejected`). The explanation is announced (role="alert").
 */
export function GuardedTextField({ error, onChange, ...props }: GuardedTextFieldProps) {
  const t = useTranslations("students");
  const [blocked, setBlocked] = useState(false);

  function onPaste(event: ClipboardEvent<HTMLInputElement>) {
    const text = event.clipboardData.getData("text");
    if (containsFullAadhaar(text)) {
      event.preventDefault();
      setBlocked(true);
    }
  }

  return (
    <div>
      <TextField
        {...props}
        error={blocked ? t("aadhaarNotAllowed") : error}
        onPaste={onPaste}
        onChange={(event) => {
          setBlocked(containsFullAadhaar(event.currentTarget.value));
          onChange?.(event);
        }}
      />
      {blocked ? (
        <p role="alert" className="sr-only">
          {t("aadhaarNotAllowed")}
        </p>
      ) : null}
    </div>
  );
}
