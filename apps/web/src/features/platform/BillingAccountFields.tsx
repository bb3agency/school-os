"use client";

import type { BillingAccount } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { TextField } from "@/components/ui/Input";
import type { FieldErrors } from "@/lib/forms";
import type { BillingField } from "./billing-account";

/** Inputs for a billing account (GSTIN/PAN upper-cased by the schema). */
export function BillingAccountFields({
  errors,
  prefix = "",
  idPrefix = "billing",
  initial,
}: {
  errors: FieldErrors;
  prefix?: string;
  idPrefix?: string;
  initial?: Partial<BillingAccount> | null;
}) {
  const t = useTranslations("platform.billingAccount");
  const name = (field: BillingField) => `${prefix}${field}`;
  const props = (field: BillingField) => ({
    id: `${idPrefix}-${field}`,
    name: name(field),
    error: errors[name(field)],
    defaultValue: (initial?.[field] as string | null | undefined) ?? undefined,
    autoComplete: "off",
  });
  return (
    <div className="space-y-4">
      <TextField {...props("legal_name")} label={t("legalName")} hint={t("legalNameHint")} />
      <div className="grid gap-4 md:grid-cols-2">
        <TextField
          {...props("state_code")}
          defaultValue={initial?.state_code ?? "37"}
          label={t("stateCode")}
          hint={t("stateCodeHint")}
          inputMode="numeric"
          maxLength={2}
        />
        <TextField
          {...props("gstin")}
          label={t("gstin")}
          hint={t("gstinHint")}
          maxLength={15}
          spellCheck={false}
        />
        <TextField {...props("pan")} label={t("pan")} hint={t("panHint")} maxLength={10} />
        <TextField {...props("billing_email")} type="email" label={t("billingEmail")} />
        <TextField {...props("billing_contact_name")} label={t("contactName")} />
        <TextField
          {...props("billing_phone")}
          type="tel"
          label={t("phone")}
          hint={t("phoneHint")}
        />
      </div>
      <TextField {...props("address_line1")} label={t("addressLine1")} />
      <TextField {...props("address_line2")} label={t("addressLine2")} />
      <div className="grid gap-4 md:grid-cols-3">
        <TextField {...props("city")} label={t("city")} />
        <TextField {...props("district")} label={t("district")} />
        <TextField
          {...props("postal_code")}
          label={t("postalCode")}
          inputMode="numeric"
          maxLength={6}
        />
      </div>
      <TextField {...props("po_reference")} label={t("poReference")} />
    </div>
  );
}
