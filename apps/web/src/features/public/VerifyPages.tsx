import type { ReactNode } from "react";
import type { PublicCertificate } from "@/server/public/verify";
import { groupCode } from "@/lib/verification-code";
import { parentDate, publicTranslator } from "@/server/public/messages";

/**
 * Certificate verification pages (ADR-0045 decisions 5-7; docs/17 §5.8; FR-CERT-021,
 * FR-CERT-022). Server components without client JavaScript: a plain form posts the code to
 * `/v/check`, which redirects to `/v/{code}`. English: nothing says which school or language
 * the code belongs to before it is found, and verifiers are receiving schools and offices.
 * The result shows exactly what the certificate prints (its frozen content) and nothing else.
 */

const t = () => publicTranslator("en");

function Shell({ children }: { children: ReactNode }) {
  const tr = t();
  return (
    <main className="public-page" lang="en">
      <title>{tr("verify.meta")}</title>
      {children}
      <footer className="public-footer">
        <p>{tr("verify.footer")}</p>
      </footer>
    </main>
  );
}

/** `/v`: one code box and "Check" (works without JavaScript). */
export function VerifyForm({ invalid = false }: { invalid?: boolean }) {
  const tr = t();
  return (
    <Shell>
      <section className="public-card" aria-labelledby="verify-heading">
        <h1 id="verify-heading">{tr("verify.title")}</h1>
        <p>{tr("verify.intro")}</p>
        <form method="post" action="/v/check" className="public-form" noValidate>
          <label htmlFor="verify-code">{tr("verify.codeLabel")}</label>
          <p id="verify-code-hint" className="public-muted">
            {tr("verify.codeHint")}
          </p>
          <input
            id="verify-code"
            name="code"
            type="text"
            inputMode="text"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={32}
            required
            aria-describedby={invalid ? "verify-code-hint verify-code-error" : "verify-code-hint"}
            aria-invalid={invalid || undefined}
            className="public-input"
          />
          {invalid ? (
            <p id="verify-code-error" className="public-error" role="alert">
              {tr("verify.invalid")}
            </p>
          ) : null}
          <button type="submit" className="public-button">
            {tr("verify.check")}
          </button>
        </form>
        <p className="public-muted">{tr("verify.noSearch")}</p>
      </section>
    </Shell>
  );
}

/** The one answer for every code that does not open a certificate (ADR-0045 decision 5). */
export function VerifyNotFound() {
  const tr = t();
  return (
    <Shell>
      <section className="public-card" aria-labelledby="verify-heading">
        <h1 id="verify-heading">{tr("verify.notFoundTitle")}</h1>
        <p>{tr("verify.notFoundBody")}</p>
        <p>
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- the public pages have
              no locale routing and work without client JavaScript: a plain link. */}
          <a href="/v">{tr("verify.again")}</a>
        </p>
      </section>
    </Shell>
  );
}

export function VerifyBusy() {
  const tr = t();
  return (
    <Shell>
      <section className="public-card" aria-labelledby="verify-heading">
        <h1 id="verify-heading">{tr("busy.title")}</h1>
        <p>{tr("verify.busyBody")}</p>
      </section>
    </Shell>
  );
}

function date(iso: string | null | undefined): string {
  return iso ? parentDate(`${iso}T00:00:00+05:30`, "en") : "";
}

/** `/v/{code}` for a live code: status banner, what the paper says it is, and its lines. */
export function VerifyResult({
  certificate,
  code,
}: {
  certificate: PublicCertificate;
  code: string;
}) {
  const tr = t();
  const c = certificate;
  const cancelled = c.status === "cancelled";
  const banner = cancelled
    ? tr("verify.cancelled", { date: date(c.cancelled_on) })
    : c.duplicate
      ? tr("verify.duplicate", {
          copy: c.duplicate.copy_no,
          serial: c.duplicate.original_serial,
          date: date(c.duplicate.issued_on),
        })
      : tr("verify.valid", { school: c.school_name, date: date(c.issued_on) });
  const lines = c.content
    ? [...c.content.fields, ...c.content.details, ...c.content.blanks].filter(
        (line) => line.label_en !== "",
      )
    : [];
  return (
    <Shell>
      <p
        className={`public-banner ${cancelled ? "public-banner-red" : c.duplicate ? "public-banner-amber" : "public-banner-green"}`}
        role="status"
      >
        {banner}
      </p>
      <section className="public-card" aria-labelledby="verify-heading">
        <h1 id="verify-heading">{c.title_en}</h1>
        <dl className="public-facts">
          <dt>{tr("verify.school")}</dt>
          <dd>
            {c.school_name}
            {c.school_place ? `, ${c.school_place}` : ""}
          </dd>
          <dt>{tr("verify.serial")}</dt>
          <dd>{c.serial}</dd>
          <dt>{tr("verify.issuedOn")}</dt>
          <dd>{date(c.issued_on)}</dd>
          <dt>{tr("verify.code")}</dt>
          <dd className="public-code">{groupCode(code)}</dd>
        </dl>
      </section>
      {lines.length > 0 ? (
        <section className="public-card" aria-labelledby="verify-lines">
          <h2 id="verify-lines">{tr("verify.printedTitle")}</h2>
          <p className="public-muted">{tr("verify.printedOnly")}</p>
          <dl className="public-lines">
            {lines.map((line) => (
              <div key={line.key}>
                <dt>{line.label_en}</dt>
                <dd>{line.value ?? tr("verify.leftBlank")}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}
      {cancelled ? <p className="public-muted">{tr("verify.cancelledNoContent")}</p> : null}
      <p className="public-muted">{tr("verify.anyoneWithCode")}</p>
    </Shell>
  );
}
