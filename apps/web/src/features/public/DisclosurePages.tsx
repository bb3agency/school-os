import type { ReactNode } from "react";
import type { PublicDisclosure } from "@/server/public/disclosure";
import { parentDate, publicTranslator } from "@/server/public/messages";
import { PublicIcon } from "./PublicIcon";

/**
 * The CBSE Appendix IX disclosure page on the public origin (ADR-0051; FR-DISC-009; docs/17
 * §5.10). Server components without client JavaScript, Register paper (public.css): the school's
 * name as masthead, "Mandatory public disclosure (CBSE Appendix IX)", sections as ruled
 * definition lists and tables, document links ("Open the PDF"), "Published by <school> on
 * <date>" and "Prepared with SchoolOS". Shows exactly the published snapshot's allowlisted
 * fields. English (the school types it in English; ADR-0036). Designed at 360 px; prints on A4.
 */

const t = () => publicTranslator("en");

type Item = PublicDisclosure["sections"][number]["items"][number];

function Shell({ school, children }: { school: string | null; children: ReactNode }) {
  const tr = t();
  return (
    <main className="public-page public-disclosure" lang="en">
      <title>{school ? tr("disclosure.metaSchool", { school }) : tr("disclosure.meta")}</title>
      <header className="public-masthead">
        <p className="public-school">{school ?? tr("disclosure.masthead")}</p>
        <p className="public-kicker">{tr("disclosure.kicker")}</p>
      </header>
      {children}
    </main>
  );
}

function ItemValue({ item, docBase }: { item: Item; docBase: string }) {
  const tr = t();
  const parts: ReactNode[] = [];
  if (item.value) parts.push(<span key="v">{item.value}</span>);
  if (item.table && item.table.rows.length) {
    parts.push(
      <div
        key="t"
        className="public-table-scroll"
        role="region"
        aria-label={item.label}
        tabIndex={0}
      >
        <table className="public-table">
          <thead>
            <tr>
              {item.table.columns.map((c) => (
                <th key={c} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {item.table.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>,
    );
  }
  if (item.documents && item.documents.length) {
    parts.push(
      <ul key="d" className="public-doc-list">
        {item.documents.map((d) => (
          <li key={d.n}>
            <a href={`${docBase}/documents/${d.n}`}>
              {d.title} <span className="public-muted">({tr("disclosure.openPdf")})</span>
            </a>
          </li>
        ))}
      </ul>,
    );
  }
  if (item.link) {
    parts.push(
      <a key="l" href={item.link} rel="noreferrer noopener">
        {item.link}
      </a>,
    );
  }
  if (!parts.length) return <dd className="public-blank">{tr("disclosure.notProvided")}</dd>;
  return <dd>{parts}</dd>;
}

/** `/d/{key}`: the published page. */
export function DisclosurePage({
  disclosure,
  docBase,
}: {
  disclosure: PublicDisclosure;
  docBase: string;
}) {
  const tr = t();
  const d = disclosure;
  const date = parentDate(d.published_at, "en");
  return (
    <Shell school={d.school_name}>
      <section className="public-card" aria-labelledby="disclosure-heading">
        <h1 id="disclosure-heading">{tr("disclosure.title")}</h1>
        <p className="public-muted">
          {tr("disclosure.published", { school: d.school_name, date })}
        </p>
      </section>
      {d.sections.map((section) => (
        <section
          key={section.key}
          className="public-card"
          aria-labelledby={`section-${section.key}`}
        >
          <h2 id={`section-${section.key}`}>{section.title}</h2>
          <dl className="public-lines">
            {section.items.map((item) => (
              <div key={item.key}>
                <dt>{item.label}</dt>
                <ItemValue item={item} docBase={docBase} />
              </div>
            ))}
          </dl>
        </section>
      ))}
      <footer className="public-footer">
        <p>{tr("disclosure.published", { school: d.school_name, date })}</p>
        <p>{tr("disclosure.prepared")}</p>
      </footer>
    </Shell>
  );
}

/** The one answer for every key that does not open a page (unknown, changed, withdrawn). */
export function DisclosureNotFound() {
  const tr = t();
  return (
    <Shell school={null}>
      <section className="public-card" aria-labelledby="disclosure-heading">
        <p className="public-muted">
          <PublicIcon name="alert" />
        </p>
        <h1 id="disclosure-heading">{tr("disclosure.notFoundTitle")}</h1>
        <p>{tr("disclosure.notFoundBody")}</p>
      </section>
    </Shell>
  );
}

export function DisclosureBusy() {
  const tr = t();
  return (
    <Shell school={null}>
      <section className="public-card" aria-labelledby="disclosure-heading">
        <p className="public-muted">
          <PublicIcon name="clock" />
        </p>
        <h1 id="disclosure-heading">{tr("busy.title")}</h1>
        <p>{tr("disclosure.busyBody")}</p>
      </section>
    </Shell>
  );
}
