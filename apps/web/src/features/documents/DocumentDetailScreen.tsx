"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useRef, type ReactNode } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Field, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Timeline, type TimelineStatus } from "@/components/ui/Timeline";
import { Value } from "@/components/ui/Value";
import { Link, useRouter } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatBytes, formatDate, formatDateTime } from "@/lib/format";
import { DOCUMENT_KEYS, useDocument } from "./data";
import {
  aclSchema,
  chosenFile,
  documentEditSchema,
  documentPatchBody,
  newVersionSchema,
  storageFailure,
} from "./forms";
import {
  aclBody,
  aclExtra,
  AclFields,
  AclSummary,
  documentFieldMap,
  DownloadButton,
  VersionReason,
  VersionStatusBadge,
} from "./parts";
import { versionBadge } from "./types";
import {
  acceptFor,
  DOC_LANGUAGES,
  DOCUMENT_PERM,
  fileKind,
  ifMatch,
  MAX_ISSUER,
  MAX_TITLE,
  isVersionBusy,
  type DocumentDetail,
  type DocumentVersion,
} from "./types";
import { createUploadKeys, StorageUploadError, uploadVersion } from "./upload";

function Item({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="font-medium text-ink">{children}</dd>
    </div>
  );
}

/** C3 files open only for staff who may see sensitive data, or the uploader (docs/09). */
function useMayOpen(
  doc: Pick<DocumentDetail, "sensitivity" | "created_by" | "uploaded_by_me">,
): boolean {
  const can = useStaffCan();
  const me = useStaffMe();
  if (doc.sensitivity !== "C3") return true;
  return can(DOCUMENT_PERM.readSensitive) || doc.uploaded_by_me || me?.user_id === doc.created_by;
}

/** The newest version that passed the virus check (what "Download" serves). */
function latestReady(versions: readonly DocumentVersion[]): DocumentVersion | undefined {
  return [...versions]
    .filter((version) => version.status === "ready")
    .sort((a, b) => b.version_no - a.version_no)[0];
}

function FileSection({ doc }: { doc: DocumentDetail }) {
  const t = useTranslations("documents.detail");
  const mayOpen = useMayOpen(doc);
  const current = doc.current_version;
  const ready = latestReady(doc.versions);
  const notice =
    current && current.status !== "ready" ? (
      isVersionBusy(current.status) ? (
        <Alert tone="info" title={t("checkingTitle", { version: current.version_no })}>
          <VersionReason version={current} />
        </Alert>
      ) : (
        <Alert tone="warning" title={t("blockedTitle", { version: current.version_no })}>
          <VersionReason version={current} />
        </Alert>
      )
    ) : null;

  if (!mayOpen) {
    return (
      <div className="space-y-3">
        {notice}
        <Alert tone="info" title={t("restrictedTitle")}>
          {t("restrictedBody")}
        </Alert>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {notice}
      {ready ? (
        <>
          <DownloadButton
            documentId={doc.id}
            versionNo={ready.version_no}
            label={t("download", { version: ready.version_no })}
          />
          {current && ready.version_no !== current.version_no ? (
            <p className="text-sm">{t("olderReady", { version: ready.version_no })}</p>
          ) : null}
          <p className="text-xs text-ink-muted">{t("linkNote")}</p>
        </>
      ) : (
        <p className="text-sm">{t("nothingReady")}</p>
      )}
    </div>
  );
}

function versionMarker(version: DocumentVersion): TimelineStatus {
  if (version.status === "ready") return "done";
  if (isVersionBusy(version.status)) return "current";
  return "pending";
}

/** Every upload of the document, newest first, as a timeline (FR-DOC-002). */
function VersionsSection({ doc }: { doc: DocumentDetail }) {
  const t = useTranslations("documents.detail");
  const tv = useTranslations("documents.version.status");
  const locale = useLocale() as Locale;
  const mayOpen = useMayOpen(doc);
  const versions = [...doc.versions].sort((a, b) => b.version_no - a.version_no);
  if (versions.length === 0) return <p className="text-sm text-ink-muted">{t("noVersions")}</p>;
  return (
    <Timeline
      label={t("versionsTitle")}
      items={versions.map((row) => ({
        id: row.id,
        status: versionMarker(row),
        statusLabel: tv(versionBadge(row)),
        title: (
          <span className="inline-flex flex-wrap items-center gap-2">
            {t("versionTitle", { version: row.version_no })}
            {doc.current_version?.id === row.id ? (
              <span className="text-xs font-normal text-ink-muted">{t("latest")}</span>
            ) : null}
          </span>
        ),
        time: <Value>{formatDateTime(row.created_at)}</Value>,
        body: (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5">
              <VersionStatusBadge version={row} />
              <Pill variant="command">{fileKind(row.mime_type) ?? t("otherKind")}</Pill>
              <Pill variant="tag">
                <span className="font-mono">
                  <Value>{formatBytes(row.size_bytes, locale)}</Value>
                </span>
              </Pill>
            </div>
            {row.status !== "ready" ? (
              <p>
                <VersionReason version={row} />
              </p>
            ) : null}
            {row.status === "ready" && mayOpen ? (
              <DownloadButton
                documentId={doc.id}
                versionNo={row.version_no}
                label={t("downloadShort")}
                description={t("downloadVersion", { version: row.version_no })}
                variant="secondary"
              />
            ) : (
              <p className="text-ink-muted">{t("cannotOpen")}</p>
            )}
          </div>
        ),
      }))}
    />
  );
}

function NewVersion({ doc }: { doc: DocumentDetail }) {
  const t = useTranslations("documents.newVersion");
  const api = useBffClient("staff");
  const keys = useRef(createUploadKeys(newIdempotencyKey));
  return (
    <ActionDialog
      triggerLabel={t("trigger")}
      title={t("title")}
      description={t("description")}
      confirmLabel={t("confirm")}
      schema={newVersionSchema(doc.purpose)}
      extra={chosenFile}
      invalidate={[DOCUMENT_KEYS.all]}
      errorNamespace="documents"
      submit={async (data, key) => {
        try {
          return await uploadVersion(api, doc.id, data.file, doc.purpose, keys.current.for(key));
        } catch (failure) {
          if (failure instanceof StorageUploadError) keys.current.storageFailed();
          throw storageFailure(failure);
        }
      }}
    >
      {(errors) => (
        <Field label={t("file")} hint={t("fileHint")} error={errors.file}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              name="file"
              type="file"
              accept={acceptFor(doc.purpose)}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border file:border-border-strong file:bg-surface file:px-3 file:py-2 file:font-semibold"
            />
          )}
        </Field>
      )}
    </ActionDialog>
  );
}

function EditAcl({ doc }: { doc: DocumentDetail }) {
  const t = useTranslations("documents.aclEdit");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("trigger")}
      title={t("title")}
      description={t("description")}
      confirmLabel={t("confirm")}
      schema={aclSchema}
      extra={aclExtra}
      fieldMap={documentFieldMap}
      invalidate={[DOCUMENT_KEYS.all]}
      errorNamespace="documents"
      submit={(data) =>
        unwrap(
          api.PUT("/api/v1/documents/{document_id}/acl", {
            params: { path: { document_id: doc.id } },
            headers: { "If-Match": ifMatch(doc.version) },
            body: { acl: aclBody(data) },
          }),
        )
      }
    >
      {(errors) => <AclFields errors={errors} initial={doc.acl} />}
    </ActionDialog>
  );
}

/** Title, type (within the purpose), language, issuer and date (PATCH; If-Match; FR-DOC-005). */
function EditDetails({ doc }: { doc: DocumentDetail }) {
  const t = useTranslations("documents.edit");
  const tn = useTranslations("documents.new");
  const ttype = useTranslations("documents.docType");
  const tlang = useTranslations("documents.language");
  const api = useBffClient("staff");
  // The API lists the types that suit the purpose (the PATCH check uses the same rule).
  const allowed = doc.allowed_doc_types ?? [];
  const types = allowed.length > 0 ? allowed : [doc.doc_type];
  return (
    <ActionDialog
      triggerLabel={t("trigger")}
      title={t("title")}
      description={t("description")}
      confirmLabel={t("confirm")}
      schema={documentEditSchema}
      fieldMap={documentFieldMap}
      invalidate={[DOCUMENT_KEYS.all]}
      errorNamespace="documents"
      submit={(data) => {
        const body = documentPatchBody(doc, data);
        if (Object.keys(body).length === 0) return Promise.resolve(doc);
        return unwrap(
          api.PATCH("/api/v1/documents/{document_id}", {
            params: { path: { document_id: doc.id } },
            headers: { "If-Match": ifMatch(doc.version) },
            body,
          }),
        );
      }}
    >
      {(errors) => (
        <>
          <TextField
            name="title"
            label={tn("titleField")}
            hint={tn("titleHint")}
            defaultValue={doc.title}
            maxLength={MAX_TITLE}
            autoComplete="off"
            required
            error={errors.title}
          />
          <SelectField
            name="doc_type"
            label={tn("docType")}
            {...(types.length === 1 ? { hint: t("typeFixed") } : {})}
            defaultValue={doc.doc_type}
            options={types.map((value) => ({ value, label: ttype(value) }))}
            error={errors.doc_type}
          />
          <SelectField
            name="language"
            label={tn("language")}
            hint={tn("languageHint")}
            placeholder={tn("languageUnknown")}
            defaultValue={doc.language ?? ""}
            options={DOC_LANGUAGES.map((value) => ({ value, label: tlang(value) }))}
            error={errors.language}
          />
          <TextField
            name="issuer"
            label={tn("issuer")}
            hint={t("issuerHint")}
            defaultValue={doc.issuer ?? ""}
            maxLength={MAX_ISSUER}
            autoComplete="off"
            error={errors.issuer}
          />
          <TextField
            name="issued_on"
            type="date"
            label={tn("issuedOn")}
            hint={t("issuedOnHint")}
            defaultValue={doc.issued_on ?? ""}
            error={errors.issued_on}
          />
        </>
      )}
    </ActionDialog>
  );
}

/**
 * Archive or bring back (POST /archive or /unarchive; `document.manage_acl`; If-Match). An
 * archived document keeps its versions and history and is listed only under "Archived".
 */
function ArchiveToggle({ doc }: { doc: DocumentDetail }) {
  const t = useTranslations("documents.archive");
  const api = useBffClient("staff");
  const archived = doc.status === "archived";
  const path = archived
    ? ("/api/v1/documents/{document_id}/unarchive" as const)
    : ("/api/v1/documents/{document_id}/archive" as const);
  return (
    <ActionDialog
      triggerLabel={t(archived ? "unTrigger" : "trigger")}
      title={t(archived ? "unTitle" : "title")}
      description={t(archived ? "unDescription" : "description")}
      confirmLabel={t(archived ? "unConfirm" : "confirm")}
      schema={z.object({})}
      invalidate={[DOCUMENT_KEYS.all]}
      errorNamespace="documents"
      submit={() =>
        unwrap(
          api.POST(path, {
            params: { path: { document_id: doc.id } },
            headers: { "If-Match": ifMatch(doc.version) },
          }),
        )
      }
    />
  );
}

function DeleteDocument({ doc }: { doc: DocumentDetail }) {
  const t = useTranslations("documents.delete");
  const api = useBffClient("staff");
  const router = useRouter();
  const queryClient = useQueryClient();
  return (
    <ActionDialog
      triggerLabel={t("trigger")}
      triggerVariant="danger"
      title={t("title")}
      description={t("description")}
      confirmLabel={t("confirm")}
      confirmVariant="danger"
      schema={z.object({})}
      errorNamespace="documents"
      submit={() =>
        unwrap(
          api.DELETE("/api/v1/documents/{document_id}", {
            params: { path: { document_id: doc.id } },
          }),
        )
      }
      onSuccess={() => {
        queryClient.removeQueries({ queryKey: DOCUMENT_KEYS.one(doc.id) });
        void queryClient.invalidateQueries({ queryKey: DOCUMENT_KEYS.all });
        router.push("/documents?deleted=1");
      }}
    >
      {() => <p className="text-sm">{t("body", { title: doc.title })}</p>}
    </ActionDialog>
  );
}

/**
 * One document (US-701 AC2..AC4, FR-DOC-002..008): download, versions with their check status
 * (virus found, withheld because it showed an Aadhaar number, still being checked …), details,
 * who can see it, a new version, editing its details, archive/unarchive and delete. Polls while
 * a version is being checked.
 */
export function DocumentDetailScreen({ documentId }: { documentId: string }) {
  const t = useTranslations("documents");
  const td = useTranslations("documents.detail");
  const ttype = useTranslations("documents.docType");
  const tpurpose = useTranslations("documents.purpose");
  const tsens = useTranslations("documents.sensitivity");
  const tlang = useTranslations("documents.language");
  const tstatus = useTranslations("documents.docStatus");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const meQuery = useStaffMeQuery();
  const allowed = can(DOCUMENT_PERM.read);
  const state = useDocument(documentId, allowed);

  const back = (
    <Link href="/documents" className="text-primary underline">
      {td("back")}
    </Link>
  );

  const crumbs = [
    { label: tn("home"), href: "/" },
    { label: t("title"), href: "/documents" },
  ];

  if (meQuery.isPending || (allowed && state.status === "loading")) {
    return <LoadingState label={tc("loading")} />;
  }
  if (!allowed) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} breadcrumb={[crumbs[0]!, { label: t("title") }]} />
        <Alert tone="warning" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      </div>
    );
  }
  if (state.status !== "ready") {
    const missing = state.status === "error" && state.reason === "not_found";
    return (
      <div className="space-y-6">
        <PageHeader title={td("title")} breadcrumb={[...crumbs, { label: td("title") }]} />
        <Alert
          tone={missing ? "warning" : "danger"}
          title={td(missing ? "notFoundTitle" : "loadErrorTitle")}
        >
          <p>{td(missing ? "notFoundBody" : "loadErrorBody")}</p>
        </Alert>
        {back}
      </div>
    );
  }

  const doc = state.data;
  const busy = doc.versions.some((version) => isVersionBusy(version.status));
  const canManage = can(DOCUMENT_PERM.manage);
  const archived = doc.status === "archived";
  const canUpload = can(DOCUMENT_PERM.upload) && doc.purpose !== "import_file" && !archived;
  const canEdit = can(DOCUMENT_PERM.upload) && !archived;
  const uploader = doc.uploaded_by_me
    ? td("you")
    : doc.uploaded_by
      ? doc.uploaded_by.display_name
      : td("formerMember");

  return (
    <div className="space-y-6">
      <PageHeader
        title={doc.title}
        breadcrumb={[...crumbs, { label: doc.title }]}
        eyebrow={ttype(doc.doc_type)}
        badge={doc.current_version ? <VersionStatusBadge version={doc.current_version} /> : null}
      />
      {/* Announces status changes while polling; the buttons stay outside the live region. */}
      <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {doc.current_version && busy ? td("stillChecking") : ""}
      </p>
      <div className="grid gap-6 xl:grid-cols-[2fr_3fr]">
        <Card title={td("fileTitle")} actions={canUpload ? <NewVersion doc={doc} /> : null}>
          <FileSection doc={doc} />
        </Card>
        <Card title={td("versionsTitle")} description={td("versionsHint")}>
          <VersionsSection doc={doc} />
        </Card>
      </div>
      {archived ? (
        <Alert tone="info" title={td("archivedTitle")}>
          {td("archivedBody")}
        </Alert>
      ) : null}
      <Card
        title={td("aboutTitle")}
        actions={
          canEdit || canManage ? (
            <span className="flex flex-wrap gap-2">
              {canEdit ? <EditDetails doc={doc} /> : null}
              {canManage ? <ArchiveToggle doc={doc} /> : null}
            </span>
          ) : null
        }
      >
        <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <Item label={td("type")}>{ttype(doc.doc_type)}</Item>
          <Item label={td("purpose")}>{tpurpose(doc.purpose)}</Item>
          <Item label={td("sensitivity")}>{tsens(`${doc.sensitivity}.short`)}</Item>
          <Item label={td("language")}>
            {doc.language ? tlang(doc.language) : tc("notAvailable")}
          </Item>
          <Item label={td("issuer")}>
            <Value>{doc.issuer}</Value>
          </Item>
          <Item label={td("issuedOn")}>
            <Value>{formatDate(doc.issued_on)}</Value>
          </Item>
          <Item label={td("status")}>{tstatus(doc.status)}</Item>
          <Item label={td("uploadedBy")}>{uploader}</Item>
          <Item label={td("created")}>
            <Value>{formatDateTime(doc.created_at)}</Value>
          </Item>
          <Item label={td("updated")}>
            <Value>{formatDateTime(doc.updated_at)}</Value>
          </Item>
        </dl>
        <p className="mt-4 text-sm text-ink-muted">{tsens(`${doc.sensitivity}.hint`)}</p>
      </Card>
      <Card
        title={td("aclTitle")}
        description={td("aclHint")}
        actions={canManage ? <EditAcl doc={doc} /> : null}
      >
        <AclSummary acl={doc.acl} />
      </Card>
      {canManage ? (
        <Card title={td("deleteTitle")} description={td("deleteHint")}>
          <DeleteDocument doc={doc} />
        </Card>
      ) : null}
    </div>
  );
}
