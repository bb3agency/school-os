"use client";

import type { TenantProfile } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useId, useState, type FormEvent } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formList, formValues } from "@/lib/forms";
import { useQueryClient } from "@tanstack/react-query";
import { ifMatch, type SettingsPatch } from "./data";

/** FR-TEN-020 (ADR-0041): the boards SchoolOS has board profiles for. */
export const BOARDS = ["BSEAP", "CBSE", "CISCE"] as const;
export type Board = (typeof BOARDS)[number];
/** Classes registered with a board (CBSE IX/XI, X/XII LOC; CISCE IX-XII; SSC X). */
export const BOARD_CLASSES = ["IX", "X", "XI", "XII"] as const;
export const MODES = ["full", "alongside"] as const;
const SAME_AS_SCHOOL = "";

const boardsSchema = z.object({
  boards: z.array(z.enum(BOARDS)).max(3),
  operating_mode: z.enum(MODES),
  current_erp_name: z
    .string()
    .trim()
    .max(80, { error: "tooLong" })
    .regex(/^[^\u0000-\u001f\u007f<>]*$/, { error: "invalid" }),
});

function isBoard(value: string): value is Board {
  return (BOARDS as readonly string[]).includes(value);
}

/** Only what changed ("send only the settings to change"). */
export function boardsPatch(
  tenant: TenantProfile,
  boards: readonly Board[],
  classBoards: Record<string, Board>,
  mode: (typeof MODES)[number],
  erpName: string,
): SettingsPatch {
  const patch: SettingsPatch = {};
  const current = tenant.boards;
  const sameBoards = current.length === boards.length && boards.every((b) => current.includes(b));
  if (!sameBoards) patch.boards = [...boards];
  const before = tenant.settings.class_boards ?? {};
  const sameClasses =
    Object.keys(before).length === Object.keys(classBoards).length &&
    Object.entries(classBoards).every(([code, board]) => before[code] === board);
  if (!sameClasses) patch.class_boards = classBoards;
  if ((tenant.settings.operating_mode ?? "full") !== mode) patch.operating_mode = mode;
  if ((tenant.settings.current_erp_name ?? "") !== erpName) patch.current_erp_name = erpName;
  return patch;
}

/**
 * Boards and operating mode (US-203, US-204; FR-TEN-020..022, ADR-0041). Holders of
 * `tenant.settings.manage` tick the school's boards, say which board Classes IX-XII follow
 * when there is more than one, and choose whether SchoolOS runs alongside the school's
 * current ERP (the Tally connector is then hidden). PATCH /tenant with If-Match; the API may
 * ask for a fresh MFA sign-in. Everyone else sees the choice read-only.
 */
export function BoardsCard({
  tenant,
  manage,
  tenantKey,
}: {
  tenant: TenantProfile;
  manage: boolean;
  tenantKey: readonly unknown[];
}) {
  const t = useTranslations("schoolSettings.boards");
  const tc = useTranslations("common");
  const tv = useTranslations("validation");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const formId = useId();
  const settings = tenant.settings;
  const known = tenant.boards.filter(isBoard);
  const [chosen, setChosen] = useState<Board[]>(known);
  const [mode, setMode] = useState<(typeof MODES)[number]>(settings.operating_mode ?? "full");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [outcome, setOutcome] = useState<"saved" | "unchanged" | null>(null);
  const classBoards = settings.class_boards ?? {};
  const boardName = (code: string) => (isBoard(code) ? t(`names.${code}`) : code);

  if (!manage) {
    return (
      <Card title={t("title")} description={t("description")}>
        <dl className="divide-y divide-border">
          <div className="grid gap-1 py-3 sm:grid-cols-[16rem_1fr] sm:gap-4">
            <dt className="text-sm text-ink-muted">{t("boardsField")}</dt>
            <dd>
              <Value>{tenant.boards.map(boardName).join(", ") || null}</Value>
            </dd>
          </div>
          {Object.keys(classBoards).length > 0 ? (
            <div className="grid gap-1 py-3 sm:grid-cols-[16rem_1fr] sm:gap-4">
              <dt className="text-sm text-ink-muted">{t("classBoardsField")}</dt>
              <dd>
                {Object.entries(classBoards)
                  .map(([code, board]) => t("classFollows", { code, board: boardName(board) }))
                  .join("; ")}
              </dd>
            </div>
          ) : null}
          <div className="grid gap-1 py-3 sm:grid-cols-[16rem_1fr] sm:gap-4">
            <dt className="text-sm text-ink-muted">{t("modeField")}</dt>
            <dd>{t(`modes.${settings.operating_mode ?? "full"}.label`)}</dd>
          </div>
        </dl>
      </Card>
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const raw = formValues(form);
    const parsed = boardsSchema.safeParse({
      boards: formList(form, "boards"),
      operating_mode: raw.operating_mode,
      current_erp_name: raw.current_erp_name ?? "",
    });
    setOutcome(null);
    setError(undefined);
    if (!parsed.success) {
      setNameError(tv(parsed.error.issues[0]?.message === "tooLong" ? "tooLong" : "invalid"));
      return;
    }
    setNameError(undefined);
    const map: Record<string, Board> = {};
    if (parsed.data.boards.length > 1) {
      for (const code of BOARD_CLASSES) {
        const value = String(raw[`class-${code}`] ?? SAME_AS_SCHOOL);
        if (isBoard(value)) map[code] = value;
      }
    }
    const patch = boardsPatch(
      tenant,
      parsed.data.boards,
      map,
      parsed.data.operating_mode,
      parsed.data.current_erp_name,
    );
    if (Object.keys(patch).length === 0) {
      setOutcome("unchanged");
      return;
    }
    setPending(true);
    unwrap(
      api.PATCH("/api/v1/tenant", {
        headers: { "If-Match": ifMatch(tenant.version) },
        body: patch,
      }),
    )
      .then(async () => {
        setOutcome("saved");
        await queryClient.invalidateQueries({ queryKey: tenantKey });
      })
      .catch((failure: unknown) => setError(failure))
      .finally(() => setPending(false));
  }

  const chip =
    "inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-md border border-border-soft bg-surface px-3 text-sm has-checked:border-primary has-checked:bg-primary-soft";

  return (
    <form noValidate onSubmit={onSubmit} aria-labelledby={`${formId}-title`}>
      <Card title={<span id={`${formId}-title`}>{t("title")}</span>} description={t("description")}>
        <div className="space-y-6">
          <fieldset className="space-y-2" aria-describedby={`${formId}-boards-hint`}>
            <legend className="text-sm font-semibold text-ink">{t("boardsField")}</legend>
            <p id={`${formId}-boards-hint`} className="text-sm text-ink-muted">
              {t("boardsHint")}
            </p>
            <div className="flex flex-wrap gap-2">
              {BOARDS.map((code) => (
                <label key={code} className={chip}>
                  <input
                    type="checkbox"
                    name="boards"
                    value={code}
                    checked={chosen.includes(code)}
                    onChange={(event) => {
                      const on = event.currentTarget.checked;
                      setChosen((list) =>
                        on ? [...list, code] : list.filter((item) => item !== code),
                      );
                    }}
                    className="size-4 accent-primary"
                  />
                  <span>{t(`names.${code}`)}</span>
                </label>
              ))}
            </div>
          </fieldset>

          {chosen.length > 1 ? (
            <fieldset className="space-y-2">
              <legend className="text-sm font-semibold text-ink">{t("classBoardsField")}</legend>
              <p className="text-sm text-ink-muted">{t("classBoardsHint")}</p>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {BOARD_CLASSES.map((code) => (
                  <SelectField
                    key={code}
                    name={`class-${code}`}
                    label={t("classLabel", { code })}
                    defaultValue={
                      classBoards[code] && chosen.includes(classBoards[code] as Board)
                        ? classBoards[code]
                        : SAME_AS_SCHOOL
                    }
                    options={[
                      { value: SAME_AS_SCHOOL, label: t("notSet") },
                      ...chosen.map((board) => ({ value: board, label: t(`names.${board}`) })),
                    ]}
                  />
                ))}
              </div>
            </fieldset>
          ) : null}

          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-ink">{t("modeField")}</legend>
            <div className="grid gap-2 md:grid-cols-2">
              {MODES.map((value) => (
                <div
                  key={value}
                  className="space-y-1 rounded-lg border border-border-soft bg-surface p-3 has-checked:border-primary has-checked:bg-primary-soft"
                >
                  <label className="flex cursor-pointer items-center gap-3 font-semibold text-ink">
                    <input
                      type="radio"
                      name="operating_mode"
                      value={value}
                      checked={mode === value}
                      onChange={() => setMode(value)}
                      aria-describedby={`${formId}-mode-${value}`}
                      className="size-4 accent-primary"
                    />
                    {t(`modes.${value}.label`)}
                  </label>
                  <p id={`${formId}-mode-${value}`} className="pl-7 text-sm text-ink-muted">
                    {t(`modes.${value}.hint`)}
                  </p>
                </div>
              ))}
            </div>
          </fieldset>

          {mode === "alongside" ? (
            <TextField
              name="current_erp_name"
              label={t("erpNameField")}
              hint={t("erpNameHint")}
              defaultValue={settings.current_erp_name ?? ""}
              maxLength={80}
              error={nameError}
              autoComplete="off"
              className="max-w-md"
            />
          ) : (
            <input type="hidden" name="current_erp_name" value={settings.current_erp_name ?? ""} />
          )}

          <ApiErrorAlert error={error} namespace="schoolSettings" />
          {outcome === "saved" ? (
            <Alert tone="success" live>
              {t("saved")}
            </Alert>
          ) : null}
          {outcome === "unchanged" ? (
            <Alert tone="info" live>
              {t("unchanged")}
            </Alert>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={pending}>
              {pending ? tc("working") : t("save")}
            </Button>
          </div>
        </div>
      </Card>
    </form>
  );
}
