"use client";

import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { useApiMutation } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { containsAadhaarNumber } from "@/lib/aadhaar";
import { text } from "@/lib/validation";
import { ASK_PERM, useKnowledgeApi, type SearchBody, type SearchResult } from "./data";
import { AskTabs, SourceChip } from "./parts";

type SearchDocType = NonNullable<SearchBody["doc_types"]>[number];

/** Document types a search may filter on (SearchIn.doc_types). */
const SEARCH_DOC_TYPES: readonly SearchDocType[] = [
  "circular",
  "policy",
  "minutes",
  "register_scan",
  "certificate",
  "letter",
  "form",
  "report",
  "verified_answer",
  "other",
];

const querySchema = text(300);

/**
 * Search documents (FR-KB-001, FR-KB-002; `document.read`): ranked passages from documents
 * you can read, without AI prose. The search text goes in the POST body, never the URL
 * (SEC-008), so this form does not use GET.
 */
export function SearchScreen() {
  const t = useTranslations("ask.search");
  const ta = useTranslations("ask");
  const tv = useTranslations("validation");
  const ttype = useTranslations("documents.docType");
  const tc = useTranslations("common");
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const api = useKnowledgeApi();
  const [error, setError] = useState<string | undefined>(undefined);
  const search = useApiMutation((body: SearchBody) => api.search(body));

  if (me.isPending) return <LoadingState label={tc("loading")} />;
  if (!can(ASK_PERM.search)) {
    return (
      <div className="space-y-6">
        <PageHeader title={ta("title")} />
        <AskTabs active="search" />
        <Alert tone="warning" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      </div>
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const parsed = querySchema.safeParse(data.get("query") ?? "");
    if (!parsed.success) {
      setError(tv(parsed.error.issues[0]?.message === "tooLong" ? "tooLong" : "required"));
      (event.currentTarget.elements.namedItem("query") as HTMLInputElement | null)?.focus();
      return;
    }
    if (containsAadhaarNumber(parsed.data)) {
      setError(tv("noAadhaar"));
      (event.currentTarget.elements.namedItem("query") as HTMLInputElement | null)?.focus();
      return;
    }
    setError(undefined);
    const docType = String(data.get("doc_type") ?? "");
    const fromDate = String(data.get("from_date") ?? "");
    search.mutate({
      query: parsed.data,
      limit: 20,
      ...(SEARCH_DOC_TYPES.includes(docType as SearchDocType)
        ? { doc_types: [docType as SearchDocType] }
        : {}),
      ...(/^\d{4}-\d{2}-\d{2}$/.test(fromDate) ? { from_date: fromDate } : {}),
    });
  }

  const results: SearchResult[] | undefined = search.data?.data;

  return (
    <div className="space-y-6">
      <PageHeader title={ta("title")} description={t("description")} />
      <AskTabs active="search" />
      <Card title={t("title")}>
        <form
          noValidate
          onSubmit={onSubmit}
          className="grid items-end gap-4 md:grid-cols-2 xl:grid-cols-4"
        >
          <TextField
            name="query"
            type="search"
            label={t("query")}
            hint={t("queryHint")}
            maxLength={300}
            error={error}
            className="md:col-span-2"
          />
          <SelectField
            name="doc_type"
            label={t("docType")}
            placeholder={tc("all")}
            defaultValue=""
            options={SEARCH_DOC_TYPES.map((value) => ({ value, label: ttype(value) }))}
          />
          <TextField name="from_date" type="date" label={t("from")} />
          <div>
            <Button type="submit" aria-disabled={search.isPending || undefined}>
              {search.isPending ? tc("working") : t("submit")}
            </Button>
          </div>
        </form>
      </Card>
      <ApiErrorAlert error={search.error ?? undefined} namespace="ask" />
      <p role="status" aria-live="polite" className="text-sm">
        {results ? t("resultsCount", { count: results.length }) : ""}
      </p>
      {results && results.length === 0 ? (
        <Alert tone="info" title={t("emptyTitle")}>
          {t("emptyBody")}
        </Alert>
      ) : null}
      {results && results.length > 0 ? (
        <section aria-label={t("results")}>
          <ol className="space-y-3">
            {results.map((result) => (
              <SearchHit key={`${result.source}-${result.snippet.slice(0, 20)}`} result={result} />
            ))}
          </ol>
        </section>
      ) : null}
    </div>
  );
}

function SearchHit({ result }: { result: SearchResult }) {
  const t = useTranslations("ask.search");
  const ttype = useTranslations("documents.docType");
  const issued = formatDate(result.issued_on);
  const docType = result.doc_type as SearchDocType;
  const type = ttype.has(docType) ? ttype(docType) : ttype("other");
  return (
    <SourceChip
      source={result.source}
      title={result.title}
      quote={result.snippet}
      meta={issued ? `${type} · ${t("issued", { date: issued })}` : type}
    />
  );
}
