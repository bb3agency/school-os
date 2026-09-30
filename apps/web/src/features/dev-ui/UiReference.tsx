import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { MinimalShell } from "@/components/shell/MinimalShell";
import { Sidebar } from "@/components/shell/Sidebar";
import { sidebarThemes } from "@/components/shell/sidebar-theme";
import { AiPanel, QuoteBlock } from "@/components/ui/AiPanel";
import { Alert } from "@/components/ui/Alert";
import { Avatar, AvatarStack } from "@/components/ui/Avatar";
import { Badge, DeltaPill, Pill } from "@/components/ui/Badge";
import { Button, IconButton } from "@/components/ui/Button";
import { Card, cardClasses } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Icon, ICON_NAMES } from "@/components/ui/Icon";
import { SearchInput, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { ProgressRing } from "@/components/ui/ProgressRing";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import type { NavSection } from "@/components/ui/SidebarNav";
import { Sparkline } from "@/components/ui/Sparkline";
import { KpiCard, StatCard } from "@/components/ui/StatCard";
import { Table, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Tabs } from "@/components/ui/Tabs";
import { Timeline } from "@/components/ui/Timeline";
import { Toggle } from "@/components/ui/Toggle";
import { ChatPreview } from "@/features/ask/ChatPreview";

/*
 * Synthetic fixture content for the component gallery (invariant 11: no real data).
 * It is sample data, not UI text, like the welcome page's HeroVisual; the page's own
 * headings come from the message files.
 */
const SWATCHES = [
  ["canvas-from", "bg-canvas-from"],
  ["canvas-to", "bg-canvas-to"],
  ["surface", "bg-surface"],
  ["surface-muted", "bg-surface-muted"],
  ["surface-sunken", "bg-surface-sunken"],
  ["border", "bg-border"],
  ["border-control", "bg-border-control"],
  ["ink", "bg-ink"],
  ["ink-muted", "bg-ink-muted"],
  ["ink-subtle", "bg-ink-subtle"],
  ["action", "bg-action"],
  ["primary", "bg-primary"],
  ["brand", "bg-brand"],
  ["success", "bg-success"],
  ["danger", "bg-danger"],
  ["warning-border", "bg-warning-border"],
  ["violet-ink", "bg-violet-ink"],
  ["teal", "bg-teal"],
  ["chart-1", "bg-chart-1"],
  ["chart-2", "bg-chart-2"],
  ["chart-3", "bg-chart-3"],
  ["chart-4", "bg-chart-4"],
  ["platform", "bg-platform"],
  ["platform-accent", "bg-platform-accent"],
] as const;

/** Sample menu for the sidebar previews (synthetic labels; nothing is current on this page). */
const SAMPLE_NAV: NavSection[] = [
  {
    id: "records",
    label: "Records",
    items: [
      { href: "/", label: "Home", exact: true, icon: "home" },
      { href: "/students", label: "Students", icon: "users" },
      { href: "/imports", label: "Import a spreadsheet", icon: "upload" },
    ],
  },
  {
    id: "checks",
    label: "Checks and submissions",
    items: [
      { href: "/findings", label: "Check before submitting", icon: "shieldCheck" },
      { href: "/exports", label: "Board and portal files", icon: "file" },
    ],
  },
];

const ROWS = [
  {
    id: "1",
    name: "Sample student A",
    cls: "7-B",
    status: "progress",
    label: "In progress",
    done: 64,
    staff: ["Sample Staff A", "Sample Staff B"],
  },
  {
    id: "2",
    name: "Sample student B",
    cls: "8-A",
    status: "review",
    label: "Review",
    done: 86,
    staff: ["Sample Staff C", "Sample Staff D", "Sample Staff E", "Sample Staff F", "Sample G"],
  },
  {
    id: "3",
    name: "Sample student C",
    cls: "9-C",
    status: "done",
    label: "Done",
    done: 100,
    staff: ["Sample Staff A"],
  },
] as const;

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={`ui-${id}`} className={`${cardClasses()} space-y-5`}>
      <h2 id={`ui-${id}`} className="text-lg font-medium text-ink">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <Eyebrow>{label}</Eyebrow>
      <div className="flex flex-wrap items-center gap-3">{children}</div>
    </div>
  );
}

/** Dev-only living reference of the design system (rendered only when dev sign-in is on). */
export function UiReference({ telugu = false }: { telugu?: boolean }) {
  const t = useTranslations("devUi");
  return (
    <MinimalShell wide>
      <div className="space-y-6">
        <PageHeader
          title={t("title")}
          description={t("intro")}
          eyebrow="SchoolOS · design system"
          breadcrumb={[{ label: "SchoolOS", href: "/" }, { label: t("title") }]}
          actions={<Pill variant="sample">{t("sampleNote")}</Pill>}
        />
        <Alert tone="warning">{t("localOnly")}</Alert>

        <Section id="colours" title={t("sections.colours")}>
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
            {SWATCHES.map(([name, className]) => (
              <li key={name} className="space-y-1.5">
                <span
                  aria-hidden="true"
                  className={`block h-12 rounded-lg border border-border ${className}`}
                />
                <code className="block font-mono text-xs text-ink-muted">{name}</code>
              </li>
            ))}
          </ul>
        </Section>

        <Section id="type" title={t("sections.type")}>
          <Eyebrow>Sentiment dynamics · line 8 / 34</Eyebrow>
          <p className="font-display text-6xl leading-none text-ink">1,245</p>
          <p className="text-2xl font-semibold text-ink">Page title, 24px semibold</p>
          <p className="text-lg font-medium text-ink">Card title, 18px medium</p>
          <p className="text-ink">Body text, 16px regular, ink.</p>
          <p className="text-sm text-ink-muted">Secondary text, 14px, ink-muted.</p>
          <p className="text-sm text-ink-subtle">1,157 last month · ink-subtle</p>
          <code className="font-mono text-sm text-ink">SYN-A-000123</code>
        </Section>

        <Section id="buttons" title={t("sections.buttons")}>
          <Row label="Variants">
            <Button>Save changes</Button>
            <Button variant="brand">Run checks</Button>
            <Button variant="secondary">Cancel</Button>
            <Button variant="ghost">View details</Button>
            <Button variant="danger">End session</Button>
            <Button disabled>Disabled</Button>
          </Row>
          <Row label="Sizes">
            <Button size="sm">Small</Button>
            <Button size="md">Medium</Button>
            <Button size="lg">Large</Button>
          </Row>
          <Row label="Icon buttons">
            <IconButton label="Add student">
              <Icon name="plus" />
            </IconButton>
            <IconButton label="Filter" variant="ghost">
              <Icon name="filter" />
            </IconButton>
            <IconButton label="Notifications, 3 unread" dot>
              <Icon name="bell" />
            </IconButton>
            <IconButton label="Send" variant="primary">
              <Icon name="send" className="size-4.5" />
            </IconButton>
            <IconButton label="Remove" variant="danger" size="sm">
              <Icon name="close" className="size-4" />
            </IconButton>
          </Row>
          <div className="ai-gradient ai-chrome rounded-xl p-5">
            <Button variant="inverse">Inverse on blue</Button>
          </div>
        </Section>

        <Section id="pills" title={t("sections.pills")}>
          <Row label="Status pills">
            <Pill variant="progress">In progress</Pill>
            <Pill variant="review">Review</Pill>
            <Pill variant="done">Done</Pill>
          </Row>
          <Row label="Chips">
            <DeltaPill value="+2.7%" direction="up" label="up 2.7% on last month" />
            <DeltaPill value="-1.2%" direction="down" />
            <Pill variant="date">
              <Icon name="calendar" className="size-3.5" />
              18 Jun
            </Pill>
            <Pill variant="tag">Class 7</Pill>
            <Pill variant="sample">Sample data</Pill>
            <Pill variant="positive">Positive</Pill>
            <Pill variant="negative">Differs</Pill>
            <Pill variant="command">/summarise</Pill>
            <Pill variant="date" size="md">
              Medium size
            </Pill>
          </Row>
          <Row label="Badges">
            <Badge>Neutral</Badge>
            <Badge tone="info">Info</Badge>
            <Badge tone="success">Active</Badge>
            <Badge tone="warning">Waiting</Badge>
            <Badge tone="danger">Suspended</Badge>
            <Badge tone="violet">Violet</Badge>
            <Badge tone="teal">Teal</Badge>
            <Badge tone="platform">Platform admin</Badge>
          </Row>
        </Section>

        <Section id="cards" title={t("sections.cards")}>
          <div className="grid gap-4 md:grid-cols-3">
            <KpiCard
              label="Students on roll"
              value="1,245"
              unavailableLabel="Not available"
              delta={{ value: "+2.7%", direction: "up", label: "up 2.7% on last month" }}
              comparison="1,212 last month"
              sparkline={
                <Sparkline
                  values={[1180, 1192, 1201, 1198, 1212, 1245]}
                  label="Rising from 1,180 to 1,245 over six months"
                />
              }
            />
            <KpiCard
              label="Records checked"
              value="86%"
              unavailableLabel="Not available"
              delta={{ value: "+4%", direction: "up" }}
              comparison="82% last week"
              aside={<ProgressRing value={86} label="Records checked" size="sm" />}
            />
            <KpiCard label="Fees collected" value={null} unavailableLabel="Not available" />
          </div>
          <dl className="grid gap-4 md:grid-cols-3">
            <StatCard label="Open findings" value="42" unavailableLabel="Not available" />
            <StatCard
              label="Imports this year"
              value="7"
              unavailableLabel="Not available"
              hint="Last on 12 June"
            />
          </dl>
          <div className="grid gap-4 md:grid-cols-2">
            <Card
              title="Mismatches to fix"
              eyebrow="Check before submitting"
              description="Sorted by portal deadline."
              actions={
                <IconButton label="Add rule" size="sm">
                  <Icon name="plus" className="size-4" />
                </IconButton>
              }
              headingLevel={3}
            >
              <p className="text-sm text-ink-muted">Card body.</p>
            </Card>
            <Card tone="muted" title="Muted card" headingLevel={3}>
              <p className="text-sm text-ink-muted">For secondary groups inside a page.</p>
            </Card>
          </div>
        </Section>

        <Section id="forms" title={t("sections.forms")}>
          <form role="search" className="flex flex-wrap items-end gap-3">
            <SearchInput
              label="Search students"
              placeholder="Search by name or admission number"
              name="q"
              wrapperClassName="min-w-64 flex-1"
            />
            <Button variant="secondary" type="submit">
              <Icon name="filter" className="size-4" />
              Filter
            </Button>
          </form>
          <div className="grid gap-4 md:grid-cols-2">
            <TextField label="School name" name="school" hint="As parents know it." />
            <TextField label="Email" name="email" error="Fill in this field." />
            <SelectField
              label="Class"
              name="class"
              placeholder="Choose a class"
              options={[
                { value: "7", label: "Class 7" },
                { value: "8", label: "Class 8" },
              ]}
            />
          </div>
          <div className="space-y-3">
            <Toggle label="Answer from documents" description="Staff can ask questions." />
            <Toggle label="Weekly summary email" defaultChecked />
            <Toggle label="Locked by the plan" disabled />
          </div>
        </Section>

        <Section id="sidebar" title={t("sections.sidebar")}>
          <p className="text-sm text-ink-muted">{t("sidebarNote")}</p>
          <div className="grid gap-4 md:grid-cols-2">
            {(["school", "platform"] as const).map((theme) => (
              <div
                key={theme}
                className={`h-[34rem] w-68 max-w-full overflow-hidden rounded-xl border border-border ${sidebarThemes[theme].surface}`}
              >
                <Sidebar
                  mode="drawer"
                  theme={theme}
                  homeHref="/"
                  navLabel={theme === "school" ? "Sample school menu" : "Sample platform menu"}
                  sections={SAMPLE_NAV}
                  context={
                    theme === "school" ? (
                      <div className="rounded-lg bg-surface-muted px-3 py-2.5">
                        <p className="text-xs text-ink-subtle">Current school</p>
                        <p className="text-sm font-semibold text-ink">Sample Model School</p>
                      </div>
                    ) : (
                      <div className="px-1.5">
                        <Badge tone="platform">Platform admin</Badge>
                      </div>
                    )
                  }
                  account={
                    <div className="flex items-center gap-2.5 px-1">
                      <Avatar name="Sample Staff A" decorative />
                      <p className="text-sm font-semibold">
                        Sample Staff A
                        <span
                          className={`block text-xs font-normal ${theme === "school" ? "text-ink-subtle" : "text-platform-muted"}`}
                        >
                          Office staff
                        </span>
                      </p>
                    </div>
                  }
                  control={null}
                />
              </div>
            ))}
          </div>
        </Section>

        <Section id="navigation" title={t("sections.navigation")}>
          <SegmentedControl
            legend="Show messages from"
            legendVisible
            options={[
              { value: "both", label: "Both" },
              { value: "office", label: "Office" },
              { value: "parent", label: "Parent" },
            ]}
          />
          <Tabs
            label="Settings"
            items={[
              { id: "basic", label: "Basic settings", panel: <p>Basic settings panel.</p> },
              { id: "tools", label: "Tools", panel: <p>Tools panel.</p> },
              { id: "schedules", label: "Schedules", panel: <p>Schedules panel.</p> },
            ]}
          />
          <Tabs
            label="Underline tabs"
            variant="underline"
            items={[
              { id: "a", label: "Overview", panel: <p>Overview panel.</p> },
              { id: "b", label: "History", panel: <p>History panel.</p> },
            ]}
          />
        </Section>

        <Section id="table" title={t("sections.table")}>
          <div className="overflow-x-auto rounded-xl border border-border">
            <Table>
              <caption className="sr-only">Sample review queue</caption>
              <THead>
                <Tr>
                  <Th>
                    <input type="checkbox" aria-label="Select all rows" />
                  </Th>
                  <Th>Student</Th>
                  <Th>Class</Th>
                  <Th>Status</Th>
                  <Th>Checked</Th>
                  <Th>Staff</Th>
                </Tr>
              </THead>
              <TBody>
                {ROWS.map((row) => (
                  <Tr key={row.id}>
                    <Td>
                      <input type="checkbox" aria-label={`Select ${row.name}`} />
                    </Td>
                    <Td>
                      <span className="flex items-center gap-3">
                        <Avatar name={row.name} decorative />
                        <span className="font-medium">{row.name}</span>
                      </span>
                    </Td>
                    <Td>{row.cls}</Td>
                    <Td>
                      <Pill variant={row.status}>{row.label}</Pill>
                    </Td>
                    <Td>
                      <ProgressRing value={row.done} label={`${row.name} checked`} size="sm" />
                    </Td>
                    <Td>
                      <AvatarStack
                        names={row.staff}
                        max={3}
                        label={`Assigned to ${row.staff.length} staff`}
                      />
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
          <div className="overflow-x-auto rounded-xl border border-border">
            <Table density="compact">
              <caption className="sr-only">Compact table</caption>
              <THead>
                <Tr>
                  <Th>Admission no.</Th>
                  <Th>Name</Th>
                  <Th>Date of birth</Th>
                </Tr>
              </THead>
              <TBody>
                <Tr>
                  <Td className="font-mono">SYN-A-0001</Td>
                  <Td>Sample student A</Td>
                  <Td>14-06-2014</Td>
                </Tr>
                <Tr>
                  <Td className="font-mono">SYN-A-0002</Td>
                  <Td>Sample student B</Td>
                  <Td>02-01-2013</Td>
                </Tr>
              </TBody>
            </Table>
          </div>
        </Section>

        <Section id="feedback" title={t("sections.feedback")}>
          <Alert tone="info" title="Info">
            The import finished. 3 rows need a look.
          </Alert>
          <Alert tone="success" title="Saved">
            The change request was sent for approval.
          </Alert>
          <Alert tone="warning" title="Check before submitting">
            2 students have a date of birth that differs.
          </Alert>
          <Alert tone="danger" title="We couldn't save this">
            Check your internet connection and try again.
          </Alert>
          <EmptyState
            title="No imports yet"
            body="Upload the admission register as Excel to start."
            action={<Button size="sm">Upload a file</Button>}
            icon="upload"
          />
          <LoadingState label="Loading…" rows={2} />
          <LoadingState label="Loading…" rows={3} variant="cards" />
        </Section>

        <Section id="people" title={t("sections.people")}>
          <Row label="Avatars">
            <Avatar name="Sample Staff A" size="sm" />
            <Avatar name="Sample Staff B" />
            <Avatar name="Sample Staff C" size="lg" />
            <AvatarStack
              names={["Sample A", "Sample B", "Sample C", "Sample D", "Sample E", "Sample F"]}
              label="Six staff"
            />
          </Row>
          <Row label="Progress rings">
            <ProgressRing value={24} label="Series 1" size="sm" color={1} />
            <ProgressRing value={58} label="Series 2" color={2} />
            <ProgressRing value={92.7} label="Series 3" size="lg" color={3} />
            <ProgressRing value={40} label="Series 4" color={4} />
          </Row>
          <Timeline
            label="Sample import history"
            items={[
              {
                id: "1",
                title: "File uploaded",
                time: "10:02 IST",
                statusLabel: "Done:",
                chips: <Pill variant="tag">register.xlsx</Pill>,
              },
              {
                id: "2",
                title: "Rows checked",
                time: "10:04 IST",
                statusLabel: "Done:",
                body: "312 rows, 3 need a look.",
              },
              {
                id: "3",
                title: "Waiting for approval",
                status: "current",
                statusLabel: "In progress:",
              },
              { id: "4", title: "Imported", status: "pending", statusLabel: "Not started:" },
            ]}
          />
        </Section>

        <Section id="ai" title={t("sections.ai")}>
          <AiPanel
            eyebrow="Ask the school"
            greeting="Good morning, Sample Staff A"
            description="Ask about your school's records and documents. Answers show their sources."
            suggestionsLabel="Try asking"
            suggestions={[
              { id: "1", label: "Fee rules for Class 7", href: "#ui-ai" },
              { id: "2", label: "Transfer certificate steps", href: "#ui-ai" },
              { id: "3", label: "Who joined in June?", href: "#ui-ai" },
            ]}
          />
          <div className={`${cardClasses()} space-y-3`}>
            <Eyebrow>Proactive · ask parent</Eyebrow>
            <QuoteBlock label="Suggested reply">
              Please bring the original birth certificate to the office this week.
            </QuoteBlock>
            <div className="flex flex-wrap gap-2">
              <Pill variant="command">/summarise</Pill>
              <Pill variant="command">/sources</Pill>
            </div>
          </div>
        </Section>

        <Section id="chat" title={t("sections.chat")}>
          <p className="text-sm text-ink-muted">{t("chatNote")}</p>
          <div className="rounded-xl border border-border p-4">
            <ChatPreview />
          </div>
        </Section>

        <Section id="dialog" title={t("sections.dialog")}>
          <Dialog
            title="Invite staff member"
            triggerLabel="Open dialog"
            closeLabel="Close"
            description="They get an email to set up their account."
            footer={<Button variant="secondary">Cancel</Button>}
          >
            <TextField label="Email" name="invite-email" />
          </Dialog>
        </Section>

        <Section id="icons" title={t("sections.icons")}>
          <ul className="grid grid-cols-3 gap-3 sm:grid-cols-6 lg:grid-cols-8">
            {ICON_NAMES.map((name) => (
              <li
                key={name}
                className="flex flex-col items-center gap-1.5 rounded-lg border border-border p-3"
              >
                <Icon name={name} className="text-ink" />
                <code className="font-mono text-[0.6875rem] text-ink-muted">{name}</code>
              </li>
            ))}
          </ul>
        </Section>

        {/* ADR-0036: the Telugu sample only while Telugu is switched on. */}
        {telugu ? (
          <Section id="telugu" title={t("sections.telugu")}>
            <div lang="te" className="space-y-3">
              <Eyebrow>తనిఖీ సారాంశం</Eyebrow>
              <p className="font-display text-4xl text-ink">విద్యార్థుల సంఖ్య</p>
              <p className="text-ink">
                విద్యార్థి వివరాలను ఒకసారి నమోదు చేయండి. పోర్టల్ కంటే ముందే తేడాలను పట్టుకోండి.
              </p>
              <div className="flex flex-wrap gap-2">
                <Pill variant="progress">పురోగతిలో ఉంది</Pill>
                <Pill variant="done">పూర్తయింది</Pill>
                <Button size="sm">మార్పులను సేవ్ చేయండి</Button>
              </div>
            </div>
          </Section>
        ) : null}
      </div>
    </MinimalShell>
  );
}
