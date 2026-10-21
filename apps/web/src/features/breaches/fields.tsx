"use client";

import { useTranslations } from "next-intl";
import { TextField } from "@/components/ui/Input";
import { BREACH_CATEGORIES } from "./types";

/**
 * The kinds of data involved, as a checkbox group (1 to 10; the API's category codes). The
 * fieldset's first box carries the id the error summary links to.
 */
export function CategoryChecklist({
  id,
  error,
  selected,
}: {
  id: string;
  error?: string | undefined;
  selected: readonly string[];
}) {
  const t = useTranslations("breaches");
  const errorId = `${id}-error`;
  return (
    <fieldset className="space-y-2" aria-describedby={error ? errorId : undefined}>
      <legend className="text-sm font-semibold text-ink">{t("form.categoriesLabel")}</legend>
      <p className="text-sm text-ink-muted">{t("form.categoriesHint")}</p>
      {error ? (
        <p id={errorId} className="text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="grid gap-1 sm:grid-cols-2">
        {BREACH_CATEGORIES.map((category, index) => (
          <label
            key={category}
            className="flex min-h-10 cursor-pointer items-start gap-2 rounded-md px-1 py-2 text-sm hover:bg-surface-muted pointer-coarse:min-h-11"
          >
            <input
              type="checkbox"
              name="categories"
              value={category}
              defaultChecked={selected.includes(category)}
              id={index === 0 ? id : undefined}
              aria-invalid={error ? true : undefined}
              className="mt-0.5 size-4 shrink-0 accent-primary"
            />
            <span>{t(`categories.${category}`)}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/** Approximate numbers of children, guardians and staff affected (figures only, no names). */
export function CountFields({
  idFor,
  errors,
  values,
}: {
  idFor: (field: string) => string;
  errors: Record<string, string>;
  values: { approx_children: number; approx_guardians: number; approx_staff: number };
}) {
  const t = useTranslations("breaches.form");
  const fields = [
    { name: "approx_children", label: t("approxChildren") },
    { name: "approx_guardians", label: t("approxGuardians") },
    { name: "approx_staff", label: t("approxStaff") },
  ] as const;
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-semibold text-ink">{t("countsLabel")}</legend>
      <p className="text-sm text-ink-muted">{t("countsHint")}</p>
      <div className="grid gap-4 sm:grid-cols-3">
        {fields.map((field) => (
          <TextField
            key={field.name}
            id={idFor(field.name)}
            name={field.name}
            label={field.label}
            error={errors[field.name]}
            defaultValue={String(values[field.name])}
            inputMode="numeric"
            maxLength={7}
            autoComplete="off"
            className="num"
          />
        ))}
      </div>
    </fieldset>
  );
}
