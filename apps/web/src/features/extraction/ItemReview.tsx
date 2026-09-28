"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField, type SelectOption } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { POLL_MS, usePolledQuery } from "@/features/imports/poll";
import { containsFullAadhaar } from "@/features/students/aadhaar";
import { isoToTypedDate, toIsoDate } from "@/features/students/dates";
import { GuardedTextField } from "@/features/students/fields";
import { FormDialog } from "@/features/students/FormDialog";
import { PERM, useStaffPermissions, type Permissions } from "@/features/students/me";
import {
  LoadGate,
  attributeLabel,
  useAttributeIndex,
  useAttributes,
} from "@/features/students/parts";
import { ProblemAlert, problemCode } from "@/features/students/ProblemAlert";
import { useSchoolStructure, useSectionOptions } from "@/features/students/StudentList";
import { STUDENT_STATUSES, type Attribute } from "@/features/students/types";
import { Link, useRouter } from "@/i18n/navigation";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { cn } from "@/lib/cn";
import { useDateInput } from "@/lib/date-format";
import { useApiForm } from "@/lib/forms";
import type { Loadable } from "@/lib/loadable";
import type { AfterAction } from "./after";
import { batchKey, itemsKey, ItemStatusBadge, pageNumber } from "./BatchScreen";
import { EXTRACTION_KEY } from "./RegisterPhotosScreen";
import {
  DATE_FIELDS,
  REGISTER_FIELDS,
  REJECT_REASONS,
  type ExtractedField,
  type ExtractionBatchDetail,
  type ExtractionItem,
  type ExtractionItemDetail,
  type ImageUnavailable,
  type ItemConfirm,
} from "./types";

export const itemKey = (id: string) => [...EXTRACTION_KEY, "item", id] as const;

export type { AfterAction };

/* ------------------------------------------------------------------ page image */

/** Seconds before the presigned image link expires when we ask for a fresh one. */
const REFRESH_MARGIN_MS = 30_000;

export function imageRefreshMs(
  item: ExtractionItemDetail | undefined,
  now = Date.now(),
): number | false {
  if (!item?.image) return false;
  const expires = Date.parse(item.image.expires_at);
  if (Number.isNaN(expires)) return false;
  return Math.max(POLL_MS, expires - now - REFRESH_MARGIN_MS);
}

/** A field's region on the page: (x, y, width, height) as fractions of the page, or null. */
export function fieldBox(
  field: ExtractedField | undefined,
): [number, number, number, number] | null {
  const box = field?.bbox;
  if (!box || box.length !== 4) return null;
  const [x, y, w, h] = box as [number, number, number, number];
  if (![x, y, w, h].every((n) => Number.isFinite(n) && n >= 0 && n <= 1) || w <= 0 || h <= 0) {
    return null;
  }
  return [x, y, w, h];
}

/** The page photo; tells the caller when the (5-minute) link stopped working. */
function PhotoImg({ src, alt, onBroken }: { src: string; alt: string; onBroken: () => void }) {
  const ref = useRef<HTMLImageElement>(null);
  useEffect(() => {
    const img = ref.current;
    if (!img) return undefined;
    img.addEventListener("error", onBroken);
    return () => img.removeEventListener("error", onBroken);
  }, [onBroken]);
  return (
    // A presigned link valid for 5 minutes: next/image would fetch and re-host it, so plain img.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      ref={ref}
      src={src}
      alt={alt}
      className="block h-auto w-full"
      referrerPolicy="no-referrer"
    />
  );
}

function PageImage({
  item,
  seq,
  redacted,
  highlight,
  onBroken,
}: {
  item: ExtractionItemDetail;
  seq: number;
  redacted: boolean;
  highlight: [number, number, number, number] | null;
  onBroken: () => void;
}) {
  const t = useTranslations("extraction.image");
  const [large, setLarge] = useState(false);
  if (!item.image) {
    const reason: ImageUnavailable = item.image_unavailable ?? "not_ready";
    return (
      <Alert
        tone={reason === "withheld_sensitive_number" ? "danger" : "info"}
        title={t(`${reason}.title`)}
      >
        {t(`${reason}.body`)}
      </Alert>
    );
  }
  return (
    <figure className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2" data-print="hide">
        <figcaption className="text-sm font-semibold">
          {t("caption", { page: seq, row: item.row_index + 1 })}
        </figcaption>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => setLarge((value) => !value)}
          aria-pressed={large}
        >
          {t("zoom")}
        </Button>
      </div>
      {redacted ? <Alert tone="info">{t("redacted")}</Alert> : null}
      <div
        role="region"
        aria-label={t("scrollLabel")}
        tabIndex={0}
        className="max-h-[70vh] overflow-auto rounded-md border border-border bg-surface-muted"
      >
        <div className={cn("relative", large ? "w-[200%]" : "w-full")}>
          <PhotoImg src={item.image.url} alt={t("alt", { page: seq })} onBroken={onBroken} />
          {highlight ? (
            <svg
              aria-hidden="true"
              viewBox="0 0 1 1"
              preserveAspectRatio="none"
              className="pointer-events-none absolute inset-0 size-full"
            >
              <rect
                x={highlight[0]}
                y={highlight[1]}
                width={highlight[2]}
                height={highlight[3]}
                fill="none"
                stroke="currentColor"
                strokeWidth={0.004}
                className="text-focus"
              />
            </svg>
          ) : null}
        </div>
      </div>
    </figure>
  );
}

/* ------------------------------------------------------------------ confirm form */

const GENDERS = ["female", "male", "transgender"] as const;

/** Register value → form value: ISO dates in the school's date format; everything else unchanged. */
export function formValueOf(key: string, field: ExtractedField | undefined): string {
  if (!field || field.masked) return "";
  return DATE_FIELDS.has(key) ? isoToTypedDate(field.value) : field.value;
}

/** The gender option the extracted text names, if any ("M", "Boy", "F", "Girl"...). */
export function genderOf(value: string): string {
  const text = value.trim().toLowerCase();
  if (["f", "female", "girl", "g"].includes(text)) return "female";
  if (["m", "male", "boy", "b"].includes(text)) return "male";
  if (["t", "transgender"].includes(text)) return "transgender";
  return "";
}

const fieldText = z
  .string()
  .trim()
  .max(200, { error: "tooLong" })
  .refine((value) => !containsFullAadhaar(value), { error: "invalid" });

const dateText = z
  .string()
  .trim()
  .refine((value) => value === "" || toIsoDate(value) !== null, { error: "invalidDate" })
  .transform((value) => (value === "" ? "" : (toIsoDate(value) ?? "")));

/** Form → ItemConfirm schema for the fields a person may edit (masked ones are never sent). */
export function confirmSchema(editable: readonly string[]) {
  const shape: Record<string, z.ZodType<string, string>> = {};
  for (const key of editable) shape[key] = DATE_FIELDS.has(key) ? dateText : fieldText;
  return z
    .object({
      ...shape,
      student_id: z.string().trim(),
      section_id: z.string().trim(),
      roll_no: z.string().trim().max(16, { error: "tooLong" }),
      student_status: z.enum(STUDENT_STATUSES, { error: "chooseOption" }),
    })
    .superRefine((data, ctx) => {
      const values = data as Record<string, string>;
      if (values.student_id === "" && editable.includes("full_name") && !values.full_name) {
        ctx.addIssue({ code: "custom", path: ["full_name"], message: "required" });
      }
      if (editable.every((key) => !values[key])) {
        ctx.addIssue({ code: "custom", path: [editable[0] ?? "full_name"], message: "required" });
      }
    });
}

/** Parsed form → POST /extraction-items/{id}/confirm body. */
export function confirmBody(
  data: Record<string, string>,
  editable: readonly string[],
): ItemConfirm {
  const fields: Record<string, string | null> = {};
  for (const key of editable) fields[key] = data[key] ? data[key] : null;
  const linking = Boolean(data.student_id);
  return {
    fields,
    student_status: (data.student_status ?? "active") as ItemConfirm["student_status"],
    ...(linking ? { student_id: data.student_id } : {}),
    ...(!linking && data.section_id ? { section_id: data.section_id } : {}),
    ...(!linking && data.section_id && data.roll_no ? { roll_no: data.roll_no } : {}),
  };
}

function ConfirmForm({
  item,
  index,
  sections,
  onFocusField,
  onDone,
}: {
  item: ExtractionItemDetail;
  index: { byKey: Map<string, Attribute>; label: (key: string) => string };
  sections: readonly SelectOption[];
  onFocusField: (key: string | null) => void;
  onDone: (action: AfterAction) => void;
}) {
  const t = useTranslations("extraction.review");
  const ts = useTranslations("students");
  const dates = useDateInput();
  const tc = useTranslations("common");
  const locale = useLocale();
  const api = useBffClient("staff");
  const [target, setTarget] = useState("");
  // Every register field is offered (the machine may have missed one); masked ones are shown
  // read-only and never sent back (the API refuses masked values).
  const keys = REGISTER_FIELDS;
  const editable = useMemo(
    () => REGISTER_FIELDS.filter((key) => !item.fields[key]?.masked),
    [item.fields],
  );
  const schema = useMemo(() => confirmSchema(editable), [editable]);
  const withheld = item.image_unavailable === "withheld_sensitive_number";

  const form = useApiForm({
    schema,
    invalidate: [itemKey(item.id), batchKey(item.batch_id), itemsKey(item.batch_id)],
    submit: (data, key) =>
      unwrap(
        api.POST("/api/v1/extraction-items/{item_id}/confirm", {
          params: { path: { item_id: item.id } },
          headers: { "Idempotency-Key": key },
          body: confirmBody(data as Record<string, string>, editable),
        }),
      ),
    // `fields.dob` → the dob input.
    fieldMap: (field) => field.replace(/^fields\./, ""),
    onSuccess: () => onDone("confirmed"),
  });

  const label = (key: string) => {
    const attribute = index.byKey.get(key);
    return attribute ? attributeLabel(attribute, key, locale) : index.label(key);
  };

  const fieldError = (key: string) =>
    form.errors[key] && DATE_FIELDS.has(key)
      ? ts("dateInvalid", dates.hint("2012-03-14"))
      : form.errors[key];

  return (
    <form noValidate onSubmit={form.onSubmit} className="space-y-5">
      <fieldset className="space-y-4">
        <legend className="mb-1 text-lg font-semibold">{t("valuesLegend")}</legend>
        <p className="text-sm text-ink-muted">{t("valuesHint")}</p>
        {keys.map((key) => {
          const field = item.fields[key];
          const low = Boolean(field?.low_confidence) || item.low_confidence_fields.includes(key);
          const fieldLabel = (
            <span className="inline-flex flex-wrap items-center gap-2">
              {label(key)}
              {low ? <Badge tone="warning">{t("checkThis")}</Badge> : null}
            </span>
          );
          const confidence =
            field?.confidence !== null && field?.confidence !== undefined
              ? t("confidence", { percent: Math.round(field.confidence * 100) })
              : undefined;
          const hint = [
            DATE_FIELDS.has(key) ? ts("dateHint", dates.hint("2012-03-14")) : null,
            low ? t("lowHint") : null,
            low ? confidence : null,
          ]
            .filter(Boolean)
            .join(" ");
          const wrapper = cn(
            low && "rounded-md border-l-4 border-warning-border bg-warning-soft/40 p-2",
          );
          if (field?.masked) {
            return (
              <div key={key} className={wrapper}>
                <p className="text-sm font-semibold">{fieldLabel}</p>
                <p className="font-mono">{field.value}</p>
                <p className="text-sm text-ink-muted">{t("maskedHint")}</p>
              </div>
            );
          }
          if (key === "gender") {
            const read = field?.value ?? "";
            return (
              <div key={key} className={wrapper}>
                <SelectField
                  name="gender"
                  label={fieldLabel}
                  hint={
                    [read && !genderOf(read) ? t("readAs", { value: read }) : null, hint || null]
                      .filter(Boolean)
                      .join(" ") || undefined
                  }
                  placeholder={tc("chooseOne")}
                  defaultValue={genderOf(read)}
                  error={form.errors.gender}
                  onFocus={() => onFocusField(key)}
                  options={GENDERS.map((value) => ({ value, label: ts(`enumValues.${value}`) }))}
                />
              </div>
            );
          }
          return (
            <div key={key} className={wrapper}>
              <GuardedTextField
                name={key}
                label={fieldLabel}
                hint={hint || undefined}
                defaultValue={formValueOf(key, field)}
                error={fieldError(key)}
                maxLength={200}
                autoComplete="off"
                spellCheck={false}
                inputMode={DATE_FIELDS.has(key) ? "numeric" : undefined}
                placeholder={DATE_FIELDS.has(key) ? dates.placeholder : undefined}
                onFocus={() => onFocusField(key)}
                onBlur={() => onFocusField(null)}
              />
            </div>
          );
        })}
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="mb-1 text-lg font-semibold">{t("studentLegend")}</legend>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            name="student_id"
            value=""
            checked={target === ""}
            onChange={() => setTarget("")}
            className="mt-1 size-4"
          />
          <span>{t("createNew")}</span>
        </label>
        {item.possible_matches.map((match) => (
          <label key={match.id} className="flex items-start gap-2 text-sm">
            <input
              type="radio"
              name="student_id"
              value={match.id}
              checked={target === match.id}
              onChange={() => setTarget(match.id)}
              className="mt-1 size-4"
            />
            <span>
              {t("addTo", {
                name: match.display_name ?? t("unnamed"),
                admission: match.admission_no ?? "—",
                classSection: match.class_section ?? t("noClass"),
              })}
            </span>
          </label>
        ))}
        {item.possible_matches.length > 0 ? (
          <p className="text-sm text-ink-muted">{t("matchesHint")}</p>
        ) : null}
        {target === "" ? (
          <div className="grid gap-4 md:grid-cols-3">
            <SelectField
              name="section_id"
              label={t("section")}
              hint={t("sectionHint")}
              placeholder={t("noSection")}
              options={sections}
              error={form.errors.section_id}
            />
            <GuardedTextField
              name="roll_no"
              label={t("rollNo")}
              maxLength={16}
              autoComplete="off"
              error={form.errors.roll_no}
            />
            <SelectField
              name="student_status"
              label={t("status")}
              defaultValue="active"
              error={form.errors.student_status}
              options={STUDENT_STATUSES.map((value) => ({ value, label: ts(`status.${value}`) }))}
            />
          </div>
        ) : (
          <input type="hidden" name="student_status" value="active" />
        )}
        {target === "" ? null : (
          <>
            <input type="hidden" name="section_id" value="" />
            <input type="hidden" name="roll_no" value="" />
          </>
        )}
      </fieldset>

      <Alert tone="info">{t("evidenceNote")}</Alert>
      <ProblemAlert
        error={form.error}
        namespace={["extraction.errors", "students.errors"]}
        action={
          target && problemCode(form.error) === "identity_change_required" ? (
            <Link
              href={`/change-requests?student_id=${encodeURIComponent(target)}`}
              className="font-semibold underline"
            >
              {t("goToChangeRequests")}
            </Link>
          ) : undefined
        }
      />
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="submit" disabled={form.pending || withheld}>
          {form.pending ? tc("working") : t("confirm")}
        </Button>
      </div>
    </form>
  );
}

const rejectSchema = z.object({ reason: z.enum(REJECT_REASONS, { error: "chooseOption" }) });

function RejectDialog({
  item,
  onDone,
}: {
  item: ExtractionItem;
  onDone: (action: AfterAction) => void;
}) {
  const t = useTranslations("extraction.reject");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  return (
    <FormDialog
      triggerLabel={t("open")}
      triggerVariant="secondary"
      title={t("title")}
      description={t("description")}
      confirmLabel={t("submit")}
      confirmVariant="danger"
      problems="extraction.errors"
      schema={rejectSchema}
      invalidate={[itemKey(item.id), batchKey(item.batch_id), itemsKey(item.batch_id)]}
      onSuccess={() => onDone("rejected")}
      submit={(data) =>
        unwrap(
          api.POST("/api/v1/extraction-items/{item_id}/reject", {
            params: { path: { item_id: item.id } },
            body: { reason: data.reason },
          }),
        )
      }
    >
      {(errors) => (
        <SelectField
          name="reason"
          label={t("reason")}
          placeholder={tc("chooseOne")}
          error={errors.reason}
          options={REJECT_REASONS.map((value) => ({ value, label: t(`reasons.${value}`) }))}
        />
      )}
    </FormDialog>
  );
}

/* ------------------------------------------------------------------ the page */

function Reviewed({ item }: { item: ExtractionItemDetail }) {
  const t = useTranslations("extraction.review");
  const tr = useTranslations("extraction.reject.reasons");
  return (
    <Card title={t("reviewedTitle")}>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
        <dt className="text-ink-muted">{t("status")}</dt>
        <dd>
          <ItemStatusBadge status={item.status} />
        </dd>
        {item.reject_reason ? (
          <>
            <dt className="text-ink-muted">{t("rejectReason")}</dt>
            <dd>{tr(item.reject_reason)}</dd>
          </>
        ) : null}
        {item.student_id ? (
          <>
            <dt className="text-ink-muted">{t("student")}</dt>
            <dd>
              <Link href={`/students/${item.student_id}`} className="text-primary underline">
                {item.created_student ? t("openCreated") : t("openStudent")}
              </Link>
            </dd>
          </>
        ) : null}
        {item.corrected_fields.length > 0 ? (
          <>
            <dt className="text-ink-muted">{t("corrected")}</dt>
            <dd>{t("correctedCount", { count: item.corrected_fields.length })}</dd>
          </>
        ) : null}
      </dl>
    </Card>
  );
}

export interface ItemReviewViewProps {
  item: Loadable<ExtractionItemDetail>;
  batch: Loadable<ExtractionBatchDetail>;
  attributes: Loadable<readonly Attribute[]>;
  permissions: Permissions;
  sections: readonly SelectOption[];
  after?: AfterAction | undefined;
  onDone: (action: AfterAction) => void;
  onImageBroken: () => void;
}

/**
 * US-402 AC1/AC2/AC4, FR-IMP-021..023, PRV-016: one extracted row beside its page photo.
 * Low-confidence fields are marked; masked values stay masked and are never sent back; a page
 * whose photo was withheld (it showed a full Aadhaar number) cannot be confirmed.
 */
export function ItemReviewView({
  item,
  batch,
  attributes,
  permissions,
  sections,
  after,
  onDone,
  onImageBroken,
}: ItemReviewViewProps) {
  const t = useTranslations("extraction.review");
  const tc = useTranslations("common");
  const index = useAttributeIndex(attributes);
  const [focused, setFocused] = useState<string | null>(null);

  if (item.status !== "ready") {
    return (
      <div className="space-y-6">
        <PageHeader title={t("loadingTitle")} />
        <LoadGate state={item} />
      </div>
    );
  }
  const data = item.data;
  const pages = batch.status === "ready" ? batch.data.pages : [];
  const page = pages.find((entry) => entry.id === data.page_id);
  const seq = pageNumber(pages, data.page_id);
  const pending = data.status === "pending_review";
  const canDecide = permissions.has(PERM.importCommit);
  const withheld = data.image_unavailable === "withheld_sensitive_number";

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title", { page: seq, row: data.row_index + 1 })}
        badge={<ItemStatusBadge status={data.status} />}
        description={t("description")}
      />
      <nav aria-label={t("relatedLabel")} data-print="hide">
        <Link href={`/register-photos/${data.batch_id}`} className="text-sm text-primary underline">
          {t("backToBatch")}
        </Link>
      </nav>
      {after ? (
        <Alert tone="success" live>
          {t(`after.${after}`)}
        </Alert>
      ) : null}
      {data.low_confidence && pending ? (
        <Alert tone="warning" title={t("lowTitle", { count: data.low_confidence_fields.length })}>
          {t("lowBody")}
        </Alert>
      ) : null}
      {data.masked ? <Alert tone="info">{t("maskedRow")}</Alert> : null}
      {withheld && pending ? (
        <Alert tone="danger" title={t("withheldTitle")}>
          {t("withheldBody")}
        </Alert>
      ) : null}
      <div className="grid gap-6 xl:grid-cols-2">
        <div className="xl:sticky xl:top-4 xl:self-start">
          <PageImage
            item={data}
            seq={seq}
            redacted={Boolean(page?.image_redacted)}
            highlight={focused ? fieldBox(data.fields[focused]) : null}
            onBroken={onImageBroken}
          />
        </div>
        <div className="space-y-4">
          {!pending ? (
            <Reviewed item={data} />
          ) : !canDecide ? (
            <Alert tone="info" title={t("noPermissionTitle")}>
              {t("noPermissionBody")}
            </Alert>
          ) : attributes.status === "loading" ? (
            <LoadingState label={tc("loading")} rows={6} />
          ) : (
            <Card>
              <div className="space-y-4">
                <ConfirmForm
                  key={data.id}
                  item={data}
                  index={index}
                  sections={sections}
                  onFocusField={setFocused}
                  onDone={onDone}
                />
                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
                  <p className="text-sm text-ink-muted">{t("rejectHint")}</p>
                  <RejectDialog item={data} onDone={onDone} />
                </div>
              </div>
            </Card>
          )}
          {!pending || !canDecide ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              {REGISTER_FIELDS.filter((key) => data.fields[key]).map((key) => (
                <div key={key} className="contents">
                  <dt className="text-ink-muted">{index.label(key)}</dt>
                  <dd>
                    <Value>{data.fields[key]?.value}</Value>
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** GET /extraction-items/{id} (fresh image link before it expires) and its batch's pages. */
export function ItemReviewScreen({ itemId, after }: { itemId: string; after?: AfterAction }) {
  const api = useBffClient("staff");
  const router = useRouter();
  const queryClient = useQueryClient();
  const permissions = useStaffPermissions();
  const attributes = useAttributes();
  const structure = useSchoolStructure();
  const sections = useSectionOptions(structure);
  const [broken, setBroken] = useState(0);
  const onImageBroken = useCallback(() => {
    // The 5-minute link expired (or failed): ask for a fresh one, but not in a loop.
    if (broken >= 2) return;
    setBroken((value) => value + 1);
    void queryClient.invalidateQueries({ queryKey: itemKey(itemId) });
  }, [broken, queryClient, itemId]);
  const item = usePolledQuery(
    itemKey(itemId),
    () =>
      unwrap(
        api.GET("/api/v1/extraction-items/{item_id}", { params: { path: { item_id: itemId } } }),
      ),
    (data) => imageRefreshMs(data),
  );
  const batchId = item.status === "ready" ? item.data.batch_id : null;
  const batch = useApiQuery(
    batchKey(batchId ?? "none"),
    () =>
      unwrap(
        api.GET("/api/v1/extraction-batches/{batch_id}", {
          params: { path: { batch_id: batchId ?? "" } },
        }),
      ),
    { enabled: batchId !== null },
  );
  return (
    <ItemReviewView
      item={item}
      batch={batch}
      attributes={attributes}
      permissions={permissions}
      sections={sections}
      after={after}
      onDone={(action) => {
        if (batchId) router.push(`/register-photos/${batchId}/next?after=${action}`);
      }}
      onImageBroken={onImageBroken}
    />
  );
}

/** Opens the next row waiting for review in a batch, or goes back to the batch when none is left. */
export function NextItemScreen({ batchId, after }: { batchId: string; after?: AfterAction }) {
  const t = useTranslations("extraction.review");
  const api = useBffClient("staff");
  const router = useRouter();
  const next = useApiQuery([...itemsKey(batchId), "next"], () =>
    unwrap(
      api.GET("/api/v1/extraction-items", {
        params: { query: { batch_id: batchId, status: "pending_review", limit: 1 } },
      }),
    ),
  );
  const target =
    next.status === "ready"
      ? next.data.data[0]
        ? `/register-photos/items/${next.data.data[0].id}${after ? `?after=${after}` : ""}`
        : `/register-photos/${batchId}`
      : null;
  useEffect(() => {
    if (target) router.replace(target);
  }, [router, target]);
  return (
    <div className="space-y-6">
      <PageHeader title={t("openingNext")} />
      {next.status === "ready" && !next.data.data[0] ? (
        <p role="status">{t("allChecked")}</p>
      ) : (
        <LoadGate state={next} />
      )}
    </div>
  );
}
