"use client";

import type { components } from "@schoolos/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { SearchInput } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import {
  CONFIGURE,
  KEYS,
  useParties,
  useParty,
  type LinkedStudent,
  type LinkFilter,
  type Party,
} from "./data";
import { Amount, TallyGate } from "./parts";

type StudentSummary = components["schemas"]["StudentSummary"];

function studentLabel(student: {
  display_name?: string | null;
  admission_no?: string | null;
  class_section?: string | null;
}): string {
  const parts = [student.admission_no, student.class_section].filter(Boolean).join(", ");
  const name = student.display_name ?? "—";
  return parts ? `${name} (${parts})` : name;
}

function LinkedList({ party }: { party: Party }) {
  const t = useTranslations("tally.ledgers");
  const api = useBffClient("staff");
  if (party.links.length === 0) return <span className="text-ink-muted">{t("notLinked")}</span>;
  return (
    <ul className="space-y-1">
      {party.links.map((student) => (
        <li key={student.student_id} className="flex flex-wrap items-center gap-2">
          <span>{studentLabel(student)}</span>
          <ActionDialog
            triggerLabel={t("unlink")}
            triggerVariant="ghost"
            triggerSize="sm"
            triggerDescription={`${party.ledger_name}: ${studentLabel(student)}`}
            title={t("unlinkTitle")}
            description={t("unlinkBody", {
              ledger: party.ledger_name,
              student: studentLabel(student),
            })}
            confirmLabel={t("unlink")}
            confirmVariant="danger"
            schema={z.object({})}
            invalidate={[KEYS.all]}
            errorNamespace="tally"
            submit={() =>
              unwrap(
                api.DELETE("/api/v1/tally/parties/{party_id}/links/{student_id}", {
                  params: { path: { party_id: party.id, student_id: student.student_id } },
                }),
              )
            }
          />
        </li>
      ))}
    </ul>
  );
}

function LinkButton({
  partyId,
  student,
  onLinked,
}: {
  partyId: string;
  student: LinkedStudent;
  onLinked: (label: string) => void;
}) {
  const t = useTranslations("tally.link");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const label = studentLabel(student);

  async function link() {
    setPending(true);
    setError(undefined);
    try {
      await unwrap(
        api.POST("/api/v1/tally/parties/{party_id}/links", {
          params: { path: { party_id: partyId } },
          body: { student_id: student.student_id },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: KEYS.all });
      onLinked(label);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <li className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 break-words">{label}</span>
        <Button
          size="sm"
          variant="secondary"
          disabled={pending}
          aria-disabled={pending || undefined}
          aria-label={t("linkTo", { student: label })}
          onClick={() => void link()}
        >
          {t("link")}
        </Button>
      </div>
      <ApiErrorAlert error={error} namespace="tally" />
    </li>
  );
}

/** Link one ledger: SchoolOS suggests students; a person chooses (the AI never links). */
function LinkPanel({ partyId, onClose }: { partyId: string; onClose: () => void }) {
  const t = useTranslations("tally.link");
  const api = useBffClient("staff");
  const party = useParty(partyId, true);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<StudentSummary[] | null>(null);
  const [searchError, setSearchError] = useState<unknown>(undefined);
  const [linked, setLinked] = useState<string | null>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, [partyId]);

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) return;
    setSearchError(undefined);
    try {
      // Names go in the body, never in a URL (SEC-008).
      const page = await unwrap(
        api.POST("/api/v1/students/search", { body: { query: query.trim(), limit: 10 } }),
      );
      setFound(page.data);
    } catch (failure) {
      setSearchError(failure);
    }
  }

  return (
    <section
      aria-labelledby={`link-${partyId}`}
      className="space-y-4 rounded-xl border border-border bg-surface p-4 shadow-card sm:p-5"
    >
      <h2
        id={`link-${partyId}`}
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold text-ink focus:outline-2 focus:outline-primary"
      >
        {t("title")}
      </h2>
      <TallyGate data={party}>
        {(detail) => (
          <div className="space-y-4">
            <p className="break-words">
              <span className="font-semibold">{detail.ledger_name}</span>{" "}
              <span className="text-ink-muted">({detail.group_name})</span>{" "}
              {detail.closing_balance === null ? null : (
                <Amount value={detail.closing_balance} withKind />
              )}
            </p>
            <p className="text-sm text-ink-muted">{t("personDecides")}</p>
            {linked ? (
              <Alert tone="success" live title={t("linkedTitle")}>
                {t("linkedBody", { student: linked })}
              </Alert>
            ) : null}
            <div className="space-y-2">
              <h3 className="font-semibold text-ink">{t("candidates")}</h3>
              {detail.candidates.length === 0 ? (
                <p className="text-sm text-ink-muted">{t("noCandidates")}</p>
              ) : (
                <ul className="space-y-2">
                  {detail.candidates.map((student) => (
                    <LinkButton
                      key={student.student_id}
                      partyId={detail.id}
                      student={student}
                      onLinked={setLinked}
                    />
                  ))}
                </ul>
              )}
            </div>
            <form role="search" onSubmit={(event) => void search(event)} className="space-y-2">
              <div className="flex flex-wrap items-end gap-2">
                <SearchInput
                  label={t("searchLabel")}
                  labelVisible
                  wrapperClassName="min-w-0 flex-1 basis-60"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  maxLength={100}
                  autoComplete="off"
                />
                <Button type="submit" variant="secondary">
                  {t("search")}
                </Button>
              </div>
              <ApiErrorAlert error={searchError} />
            </form>
            {found !== null ? (
              found.length === 0 ? (
                <p className="text-sm text-ink-muted">{t("noResults")}</p>
              ) : (
                <ul className="space-y-2" aria-label={t("results")}>
                  {found.map((student) => (
                    <LinkButton
                      key={student.id}
                      partyId={detail.id}
                      student={{
                        student_id: student.id,
                        display_name: student.display_name,
                        admission_no: student.admission_no,
                        class_section: student.class_section,
                      }}
                      onLinked={setLinked}
                    />
                  ))}
                </ul>
              )
            ) : null}
          </div>
        )}
      </TallyGate>
      <Button variant="ghost" onClick={onClose}>
        {t("close")}
      </Button>
    </section>
  );
}

/**
 * Link Tally ledgers to students (M6, FR-TALLY-006; `tally.configure`): the ledgers of the
 * last snapshot, unlinked first. Only linked ledgers count for a student's dues and for Ask the
 * school; SchoolOS never links by itself.
 */
export function TallyLedgersScreen() {
  const t = useTranslations("tally.ledgers");
  const tt = useTranslations("tally");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(CONFIGURE);
  const [filter, setFilter] = useState<LinkFilter>("unlinked");
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [open, setOpen] = useState<string | null>(null);
  const cursor = cursors[cursors.length - 1] ?? null;
  const parties = useParties(filter, search, cursor, meLoaded && allowed);

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCursors([null]);
    setSearch(draft.trim());
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: tt("connector.title"), href: "/settings/tally" },
          { label: t("title") },
        ]}
      />
      {meLoaded && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-4">
            <SegmentedControl
              legend={t("filter")}
              options={[
                { value: "unlinked", label: t("filters.unlinked") },
                { value: "linked", label: t("filters.linked") },
                { value: "all", label: t("filters.all") },
              ]}
              value={filter}
              onValueChange={(value) => {
                setCursors([null]);
                setFilter(value as LinkFilter);
              }}
            />
            <form role="search" onSubmit={submitSearch} className="flex flex-wrap items-end gap-2">
              <SearchInput
                label={t("searchLabel")}
                labelVisible
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                maxLength={100}
                autoComplete="off"
              />
              <Button type="submit" variant="secondary">
                {t("search")}
              </Button>
            </form>
          </div>
          {open ? <LinkPanel partyId={open} onClose={() => setOpen(null)} /> : null}
          <Card title={t("listTitle")}>
            <TallyGate data={meLoaded ? parties : { status: "loading" }}>
              {(page) =>
                page.data.length === 0 ? (
                  <EmptyState icon="inbox" title={t("emptyTitle")} body={t("emptyBody")} />
                ) : (
                  <>
                    <TableScroll label={t("listTitle")}>
                      <Table>
                        <THead>
                          <Tr>
                            <Th>{t("columns.ledger")}</Th>
                            <Th>{t("columns.group")}</Th>
                            <Th className="text-right">{t("columns.balance")}</Th>
                            <Th>{t("columns.students")}</Th>
                            <Th>
                              <span className="sr-only">{t("columns.actions")}</span>
                            </Th>
                          </Tr>
                        </THead>
                        <TBody>
                          {page.data.map((party) => (
                            <Tr key={party.id}>
                              <Td className="break-words">
                                <span className="font-semibold">{party.ledger_name}</span>
                                <span className="block text-xs text-ink-muted">
                                  {t("asOf", { date: formatDate(party.as_of) ?? "" })}
                                </span>
                              </Td>
                              <Td>{party.group_name}</Td>
                              <Td className="text-right">
                                {party.closing_balance === null ? (
                                  <span className="text-ink-muted">—</span>
                                ) : (
                                  <Amount value={party.closing_balance} withKind />
                                )}
                              </Td>
                              <Td>
                                <LinkedList party={party} />
                              </Td>
                              <Td>
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  aria-label={t("linkLedger", { ledger: party.ledger_name })}
                                  onClick={() => setOpen(party.id)}
                                >
                                  {t("link")}
                                </Button>
                              </Td>
                            </Tr>
                          ))}
                        </TBody>
                      </Table>
                    </TableScroll>
                    <div className="mt-4 flex flex-wrap gap-3">
                      {cursors.length > 1 ? (
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => setCursors((all) => all.slice(0, -1))}
                        >
                          {tt("previous")}
                        </Button>
                      ) : null}
                      {page.next_cursor ? (
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() =>
                            setCursors((all) => [...all, page.next_cursor as string | null])
                          }
                        >
                          {tt("next")}
                        </Button>
                      ) : null}
                    </div>
                  </>
                )
              }
            </TallyGate>
          </Card>
        </>
      )}
    </div>
  );
}
