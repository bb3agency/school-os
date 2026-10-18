"use client";

import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console/DataTableV2";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Eyebrow } from "@/components/ui/Eyebrow";
import type { FilterChip } from "@/components/ui/FilterChips";
import { Illustration, ILLUSTRATIONS, type IllustrationName } from "@/components/ui/Illustration";
import { SearchInput, TextField } from "@/components/ui/Input";
import { Kbd } from "@/components/ui/Kbd";
import { Menu } from "@/components/ui/Menu";
import { Pagination } from "@/components/ui/Pagination";
import { Popover } from "@/components/ui/Popover";
import { SaveBar } from "@/components/ui/SaveBar";
import { Steps } from "@/components/ui/Steps";
import { ToastProvider, useToast } from "@/components/ui/Toaster";
import { cn } from "@/lib/cn";
import type { Loadable } from "@/lib/loadable";

/*
 * The v1.0 console components and the two visual directions, live (docs/17 §4, §5.6;
 * report §8.7: this page is the living reference). Synthetic content only (invariant
 * 11); sample values are fixtures, the section headings come from the message files.
 */

interface SampleDue {
  id: string;
  name: string;
  cls: string;
  due: string;
  status: "due" | "paid";
}

const DUES: readonly SampleDue[] = [
  { id: "a", name: "Sample student A", cls: "7-B", due: "₹12,400", status: "due" },
  { id: "b", name: "Sample student B", cls: "7-B", due: "₹0", status: "paid" },
  { id: "c", name: "Sample student C", cls: "8-A", due: "₹3,250", status: "due" },
];

function Block({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <Eyebrow>{label}</Eyebrow>
      {children}
    </div>
  );
}

function ToastButton() {
  const toast = useToast();
  return (
    <Button
      variant="secondary"
      onClick={() =>
        toast.show({ title: "Receipt sent", body: "Sample student A", tone: "success" })
      }
    >
      Show a toast
    </Button>
  );
}

/** Interactive v1.0 components (client): table, filters, forms, overlays, toasts. */
export function ConsoleReference() {
  const [chips, setChips] = useState<FilterChip[]>([
    { id: "class", label: "Class", value: "7-B" },
    { id: "status", label: "Status", value: "Due" },
  ]);
  const [drawer, setDrawer] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const state: Loadable<readonly SampleDue[]> = { status: "ready", data: DUES };
  const columns: ColumnV2<SampleDue>[] = [
    { key: "name", header: "Student", cell: (row) => row.name },
    { key: "cls", header: "Class", name: "Class", cell: (row) => row.cls },
    { key: "due", header: "Due (₹)", name: "Due", numeric: true, cell: (row) => row.due },
    {
      key: "status",
      header: "Status",
      name: "Status",
      cell: (row) =>
        row.status === "due" ? (
          <Badge tone="warning">Due</Badge>
        ) : (
          <Badge tone="success">Paid</Badge>
        ),
    },
    {
      key: "actions",
      header: "",
      hideable: false,
      cell: (row) => (
        <Menu
          label={`Actions for ${row.name}`}
          entries={[
            { id: "open", label: "Open record", onSelect: () => setDrawer(true) },
            { id: "receipt", label: "Print receipt", onSelect: () => undefined },
          ]}
        />
      ),
    },
  ];
  return (
    <ToastProvider>
      <div className="space-y-8">
        <Block label="DataTableV2 + FilterChips + Pagination">
          <DataTableV2
            caption="Fee dues (sample)"
            columns={columns}
            state={state}
            rowKey={(row) => row.id}
            toolbar={
              <form role="search" className="flex flex-wrap items-end gap-3">
                <SearchInput label="Search students" name="q" wrapperClassName="min-w-56 flex-1" />
                <Button type="button" variant="secondary">
                  Filter
                </Button>
              </form>
            }
            chips={chips}
            onRemoveChip={(id) => setChips((list) => list.filter((chip) => chip.id !== id))}
            onClearFilters={() => setChips([])}
            emptyTitle="No dues"
            selection={{
              rowLabel: (row) => `Select ${row.name}`,
              actions: (selected, clear) => (
                <Button variant="secondary" size="sm" onClick={clear}>
                  Send reminder ({selected.length})
                </Button>
              ),
            }}
            footer={
              <Pagination
                from={1}
                to={3}
                total={3}
                hasPrevious={false}
                hasNext={false}
                onPrevious={() => undefined}
                onNext={() => undefined}
              />
            }
            stacked
          />
        </Block>

        <Block label="Steps, ErrorSummary, SaveBar">
          <Steps
            label="Provisioning steps (sample)"
            current={1}
            steps={[
              { id: "school", label: "School" },
              { id: "plan", label: "Plan" },
              { id: "admin", label: "First admin" },
            ]}
          />
          <form
            noValidate
            className="space-y-4 rounded-xl border border-border bg-surface p-4 sm:p-5"
            onSubmit={(event) => {
              event.preventDefault();
              setAttempt((n) => n + 1);
            }}
          >
            {attempt > 0 ? (
              <ErrorSummary
                attempt={attempt}
                errors={[{ fieldId: "ui-sample-name", message: "Enter the school's name" }]}
              />
            ) : null}
            <TextField
              id="ui-sample-name"
              label="School name"
              name="name"
              onChange={() => setDirty(true)}
              error={attempt > 0 ? "Enter the school's name" : undefined}
            />
            <SaveBar dirty={dirty}>
              <Button type="button" variant="secondary" onClick={() => setDirty(false)}>
                Discard
              </Button>
              <Button type="submit">Save</Button>
            </SaveBar>
          </form>
        </Block>

        <Block label="Menu, Popover, Drawer, toast, Kbd">
          <div className="flex flex-wrap items-center gap-3">
            <Menu
              label="More"
              variant="button"
              trigger="More"
              entries={[
                { id: "export", label: "Export", icon: "file", onSelect: () => undefined },
                { kind: "separator", id: "sep" },
                { id: "remove", label: "Remove", danger: true, onSelect: () => undefined },
              ]}
            />
            <Popover trigger="Columns" title="Show columns">
              <p className="text-sm text-ink-muted">Popover content (sample).</p>
            </Popover>
            <Button variant="secondary" onClick={() => setDrawer(true)}>
              Open drawer
            </Button>
            <ToastButton />
            <span className="text-sm text-ink-muted">
              <Kbd>Ctrl</Kbd> <Kbd>K</Kbd>
            </span>
          </div>
          <Drawer
            open={drawer}
            onClose={() => setDrawer(false)}
            title="Sample student A"
            description="Class 7-B"
            closeLabel="Close"
          >
            <p className="text-sm text-ink-muted">Record details (sample).</p>
          </Drawer>
        </Block>
      </div>
    </ToastProvider>
  );
}

/** Figures: amounts, counts and dates in SOS Figures (tabular), IDs in mono. */
export function FiguresReference() {
  return (
    <dl className="grid gap-3 sm:grid-cols-3">
      {[
        ["Amount", "₹1,24,500.00", "num text-2xl font-semibold"],
        ["Count", "1,240", "num text-2xl font-semibold"],
        ["Admission number", "ADM/2019/0412", "font-mono text-lg"],
      ].map(([label, value, className]) => (
        <div key={label} className="rounded-xl border border-border bg-surface p-4">
          <dt className="text-sm text-ink-muted">{label}</dt>
          <dd className={cn("mt-1 text-ink", className)}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Every illustration, decorative, with the file name as the sample caption. */
export function IllustrationsReference() {
  const t = useTranslations("devUi");
  return (
    <div className="space-y-4">
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {(Object.keys(ILLUSTRATIONS) as IllustrationName[]).map((name) => (
          <li key={name} className="rounded-xl border border-border bg-canvas p-3">
            <Illustration name={name} />
            <p className="mt-2 font-mono text-xs text-ink-muted">{name}.svg</p>
          </li>
        ))}
      </ul>
      <EmptyState
        illustration="records-almirah"
        title={t("emptyIllustrated")}
        body={t("emptyIllustratedBody")}
      />
    </div>
  );
}
