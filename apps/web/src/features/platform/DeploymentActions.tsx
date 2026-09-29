"use client";

import type { Deployment } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { TextField } from "@/components/ui/Input";
import { SecretOnce } from "@/components/ui/SecretOnce";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { HOST_REF_PATTERN, VERSION_PATTERN, optionalDomain } from "@/lib/validation";
import { PK, ifMatch, useCan } from "./data";

const optionalPattern = (pattern: RegExp, error: string) =>
  z
    .string()
    .trim()
    .refine((value) => value === "" || pattern.test(value), { error })
    .transform((value) => (value === "" ? null : value));

const editSchema = z.object({
  target_version: optionalPattern(VERSION_PATTERN, "invalidVersion"),
  custom_domain: optionalDomain,
  hostname: optionalDomain,
  host_ref: optionalPattern(HOST_REF_PATTERN, "invalidHostRef"),
});

const INVALIDATE = [PK.deployments, PK.versions, PK.tenants, PK.dashboard] as const;

/**
 * FR-PLT-023..025 (docs/16 §5.12): set target version / domain / host, rotate the heartbeat
 * key (shown ONCE) and decommission. `platform.fleet.manage` with step-up MFA.
 */
export function DeploymentActions({ deployment }: { deployment: Deployment }) {
  const t = useTranslations("platform.fleet");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const can = useCan();
  if (!can("platform.fleet.manage") || deployment.status === "decommissioned") return null;
  const path = { deployment_id: deployment.id };
  const label = `${deployment.school_name} (${deployment.tenant_code})`;
  const dedicated = deployment.mode === "dedicated";

  return (
    <div className="relative flex flex-wrap gap-2">
      <ActionDialog
        triggerLabel={t("edit")}
        triggerSize="sm"
        triggerDescription={label}
        title={t("editTitle")}
        confirmLabel={tc("save")}
        stepUp
        schema={editSchema}
        invalidate={INVALIDATE}
        submit={(data) =>
          unwrap(
            api.PATCH("/api/v1/platform/deployments/{deployment_id}", {
              params: { path, header: { "If-Match": ifMatch(deployment.version) } },
              body: dedicated ? data : { target_version: data.target_version },
            }),
          )
        }
      >
        {(errors) => (
          <>
            <TextField
              name="target_version"
              label={t("targetVersion")}
              hint={t("targetVersionHint")}
              error={errors.target_version}
              defaultValue={deployment.target_version ?? ""}
              spellCheck={false}
              autoComplete="off"
            />
            {dedicated ? (
              <>
                <TextField
                  name="custom_domain"
                  label={t("colDomain")}
                  hint={t("customDomainHint")}
                  error={errors.custom_domain}
                  defaultValue={deployment.custom_domain ?? ""}
                  spellCheck={false}
                  autoComplete="off"
                />
                <TextField
                  name="hostname"
                  label={t("hostname")}
                  error={errors.hostname}
                  defaultValue={deployment.hostname ?? ""}
                  spellCheck={false}
                  autoComplete="off"
                />
                <TextField
                  name="host_ref"
                  label={t("hostRef")}
                  hint={t("hostRefHint")}
                  error={errors.host_ref}
                  defaultValue={deployment.host_ref ?? ""}
                  spellCheck={false}
                  autoComplete="off"
                />
              </>
            ) : null}
          </>
        )}
      </ActionDialog>
      {dedicated ? (
        <ActionDialog
          triggerLabel={t("rotateKey")}
          triggerSize="sm"
          triggerDescription={label}
          title={t("rotateKeyTitle")}
          description={t("rotateKeyBody")}
          confirmLabel={t("rotateKey")}
          stepUp
          schema={z.object({})}
          invalidate={INVALIDATE}
          submit={() =>
            unwrap(
              api.POST("/api/v1/platform/deployments/{deployment_id}/heartbeat-key:rotate", {
                params: { path },
              }),
            )
          }
          renderResult={(result, close) => (
            <SecretOnce
              label={t("heartbeatKey")}
              secret={result.heartbeat_key}
              keyId={result.heartbeat_key_id}
              doneLabel={tc("done")}
              onDone={close}
            />
          )}
        />
      ) : null}
      <ActionDialog
        triggerLabel={t("decommission")}
        triggerSize="sm"
        triggerVariant="danger"
        triggerDescription={label}
        title={t("decommissionTitle")}
        description={t("decommissionBody")}
        confirmLabel={t("decommission")}
        confirmVariant="danger"
        stepUp
        schema={z.object({})}
        invalidate={INVALIDATE}
        submit={() =>
          unwrap(
            api.POST("/api/v1/platform/deployments/{deployment_id}/decommission", {
              params: { path },
            }),
          )
        }
      />
    </div>
  );
}
