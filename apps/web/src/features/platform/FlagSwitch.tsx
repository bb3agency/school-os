"use client";

import type { FeatureFlag } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useId, useRef, useState } from "react";
import { z } from "zod";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Toggle } from "@/components/ui/Toggle";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useDialogClose } from "@/lib/dialog-motion";
import { useApiForm } from "@/lib/forms";
import { requiredInt } from "@/lib/validation";
import { PK, ifMatch } from "./data";

/**
 * On/off switch for a global flag in the flags table (FR-PLT-022). Flipping it does not
 * change anything yet: it opens a confirmation (native <dialog>, focus trapped, Escape
 * cancels, focus back on the switch), and only "Turn on"/"Turn off" sends PUT
 * /platform/flags/{key} with the flag's other settings unchanged. Step-up MFA may be asked.
 * Without platform.flags.manage the state shows as a plain On/Off pill.
 */
export function FlagSwitch({ flag, manage }: { flag: FeatureFlag; manage: boolean }) {
  const t = useTranslations("platform.flags");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const bodyId = useId();
  const switchId = `flag-switch-${useId()}`;
  const [open, setOpen] = useState(false);
  const next = !flag.enabled;
  // Fades out before it closes (Escape too); instant under reduced motion (docs/17 §5.5).
  const close = useDialogClose(dialogRef, "modal");

  const form = useApiForm({
    // The version read when the dialog opened goes in If-Match (AA-13): a flag changed by
    // someone else meanwhile answers 412 instead of being overwritten.
    schema: z.object({ version: requiredInt(1, 2_147_483_647) }),
    invalidate: [PK.flags],
    submit: ({ version }) =>
      unwrap(
        api.PUT("/api/v1/platform/flags/{key}", {
          params: { path: { key: flag.key }, header: { "If-Match": ifMatch(version) } },
          body: {
            enabled: next,
            description: flag.description,
            rollout_percent: flag.rollout_percent,
          },
        }),
      ),
    onSuccess: () => close(),
  });

  if (!manage) {
    return (
      <Pill variant={flag.enabled ? "positive" : "tag"}>
        {flag.enabled ? t("stateOn") : t("stateOff")}
      </Pill>
    );
  }

  return (
    <>
      <Toggle
        id={switchId}
        label={
          <>
            {flag.enabled ? t("stateOn") : t("stateOff")}
            <span className="sr-only">: {flag.key}</span>
          </>
        }
        checked={flag.enabled}
        onCheckedChange={() => {
          form.reset();
          setOpen(true);
          dialogRef.current?.showModal();
        }}
      />
      <dialog
        ref={dialogRef}
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        onClose={() => {
          setOpen(false);
          document.getElementById(switchId)?.focus();
        }}
        className="dialog-motion m-auto w-[min(32rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-0 text-ink shadow-popover"
      >
        {open ? (
          <form noValidate onSubmit={form.onSubmit}>
            <input type="hidden" name="version" defaultValue={flag.version} />
            <div className="space-y-2 p-6 pb-2">
              <h2 id={titleId} className="text-lg font-semibold">
                {next ? t("turnOnTitle", { key: flag.key }) : t("turnOffTitle", { key: flag.key })}
              </h2>
              <p id={bodyId} className="text-sm text-ink-muted">
                {next ? t("turnOnBody") : t("turnOffBody")}
              </p>
            </div>
            <div className="space-y-4 px-6 py-4">
              <p className="text-sm text-ink-muted">{tc("stepUpNote")}</p>
              <ApiErrorAlert error={form.error} />
            </div>
            <div className="dialog-footer flex flex-wrap justify-end gap-2 rounded-b-xl border-t border-border bg-surface-muted px-6 py-4">
              <Button variant="secondary" onClick={close}>
                {tc("cancel")}
              </Button>
              <Button
                type="submit"
                variant={next ? "primary" : "danger"}
                disabled={form.pending}
                aria-disabled={form.pending || undefined}
                loading={form.pending}
              >
                {form.pending ? tc("working") : next ? t("turnOn") : t("turnOff")}
              </Button>
            </div>
          </form>
        ) : null}
      </dialog>
    </>
  );
}
