"use client";

import { PLATFORM_ROLES } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";

const inviteSchema = z.object({
  email: z
    .string()
    .trim()
    .min(1, { error: "required" })
    .pipe(z.email({ error: "invalidEmail" })),
  role: z.enum(PLATFORM_ROLES, { error: "chooseOption" }),
});

type InviteErrors = Partial<Record<"email" | "role", "required" | "invalidEmail" | "chooseOption">>;

/** FR-PLT-028: invite operator (step-up MFA enforced by the API when wired). */
export function InviteOperatorForm() {
  const t = useTranslations("platform.operators");
  const tv = useTranslations("platform.validation");
  const tc = useTranslations("common");
  const [errors, setErrors] = useState<InviteErrors>({});
  const [submitted, setSubmitted] = useState(false);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const result = inviteSchema.safeParse({
      email: data.get("email") ?? "",
      role: data.get("role") ?? "",
    });
    if (result.success) {
      setErrors({});
      setSubmitted(true);
      return;
    }
    const next: InviteErrors = {};
    for (const issue of result.error.issues) {
      const field = issue.path[0];
      const key = issue.message;
      if ((field === "email" || field === "role") && !next[field]) {
        next[field] = key === "invalidEmail" || key === "chooseOption" ? key : "required";
      }
    }
    setErrors(next);
    setSubmitted(false);
  }

  return (
    <form noValidate onSubmit={onSubmit} className="space-y-4">
      <TextField
        name="email"
        type="email"
        label={t("inviteEmail")}
        error={errors.email ? tv(errors.email) : undefined}
        autoComplete="off"
      />
      <SelectField
        name="role"
        label={t("inviteRole")}
        error={errors.role ? tv(errors.role) : undefined}
        placeholder={tv("chooseOption")}
        options={PLATFORM_ROLES.map((role) => ({ value: role, label: t(`roles.${role}`) }))}
        defaultValue=""
      />
      <p className="text-sm text-ink-muted">{t("stepUpNote")}</p>
      {submitted ? (
        <Alert tone="warning" live>
          {tc("notConnected")}
        </Alert>
      ) : null}
      <div className="flex justify-end">
        <Button type="submit">{t("inviteSend")}</Button>
      </div>
    </form>
  );
}
