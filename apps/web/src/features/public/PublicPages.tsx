import type { components } from "@schoolos/api-client";
import type { PayError } from "@/server/public/actions";
import {
  parentDate,
  parentDay,
  parentRupees,
  parentTranslator,
  publicTranslator,
  PUBLIC_TELUGU_STYLESHEET_HREF,
  type ParentLanguage,
} from "@/server/public/messages";
import type { LinkPage } from "@/server/public/view";
import { PublicIcon } from "./PublicIcon";

/**
 * Parent pages (ADR-0046 decision 2; docs/17 §5.8; wave R2-3H). Server components without client
 * JavaScript, in Direction B "Register paper" (public.css): the school's name as the masthead
 * over a double rule, one ruled sheet with the page's content, and at the bottom "From <school>,
 * sent through SchoolOS", the link's expiry and the forwarding warning, in the parent language.
 * Forms are plain HTML posts (`/p/pay`, `/p/optin`) with the CSRF token bound to the cookie;
 * the PDF is a plain link (`/p/document`). Designed at 360 px for cheap phones on 2G/3G.
 */

export type LinkEnvelope = LinkPage;
type Notice = components["schemas"]["ParentNoticeOut"];
type Receipt = components["schemas"]["ParentReceiptOut"];
type Dues = components["schemas"]["ParentDuesOut"];
type Optin = components["schemas"]["ParentOptinOut"];
type Payment = components["schemas"]["ParentPaymentStatusOut"];
type T = ReturnType<typeof parentTranslator>;

/** A one-line message from the page's own forms (reason codes from the query). */
export type PageNotice =
  { kind: "pay"; error: PayError } | { kind: "pdf" } | { kind: "saved"; ok: boolean };

/** The self-hosted Telugu font, only on Telugu pages (ADR-0044 decision 3). */
function TeluguFont({ language }: { language: ParentLanguage }) {
  if (language !== "te") return null;
  return <link rel="stylesheet" href={PUBLIC_TELUGU_STYLESHEET_HREF} precedence="default" />;
}

function Frame({ envelope, children }: { envelope: LinkEnvelope; children: React.ReactNode }) {
  const { language } = envelope;
  const t = publicTranslator(language);
  return (
    <main className="public-page" lang={language}>
      <TeluguFont language={language} />
      <title>{t("meta.title")}</title>
      <header className="public-masthead">
        <p className="public-school">{envelope.school_name}</p>
        <p className="public-kicker">{t(`kind.${envelope.kind}`)}</p>
      </header>
      {children}
      <footer className="public-footer">
        <p>{t("footer.fromSchool", { school: envelope.school_name })}</p>
        <p>{t("view.validUntil", { date: parentDate(envelope.expires_at, language) })}</p>
        <p className="public-warning">{t("footer.doNotForward")}</p>
      </footer>
    </main>
  );
}

function Banner({
  tone,
  icon,
  children,
}: {
  tone: "green" | "amber" | "red";
  icon: "valid" | "alert" | "cancelled" | "clock";
  children: React.ReactNode;
}) {
  return (
    <p
      className={`public-banner public-banner-${tone}`}
      role={tone === "green" ? "status" : "alert"}
    >
      <PublicIcon name={icon} />
      <span>{children}</span>
    </p>
  );
}

function Csrf({ value }: { value: string }) {
  return <input type="hidden" name="csrf" value={value} />;
}

// --- notice ------------------------------------------------------------------------------------

function NoticeSheet({ notice, t, flash }: { notice: Notice; t: T; flash: PageNotice | null }) {
  const paragraphs = notice.body.split(/\n\s*\n/).filter((p) => p.trim() !== "");
  return (
    <section className="public-card" aria-labelledby="public-heading">
      <h1 id="public-heading">{notice.title}</h1>
      {notice.date ? (
        <p className="public-muted">{t("notice.dated", { date: parentDay(notice.date) })}</p>
      ) : null}
      <div className="public-prose">
        {paragraphs.map((p, i) => (
          <p key={i}>{p}</p>
        ))}
      </div>
      {flash?.kind === "pdf" ? (
        <Banner tone="amber" icon="alert">
          {t("common.pdfUnavailable")}
        </Banner>
      ) : null}
      {notice.has_pdf ? (
        // eslint-disable-next-line @next/next/no-html-link-for-pages -- a plain link: the parent pages ship no client router (docs/17 §5.8)
        <a className="public-button" href="/p/document">
          {t("common.openPdf")}
        </a>
      ) : null}
    </section>
  );
}

// --- receipt -----------------------------------------------------------------------------------

const MODES = new Set(["cash", "cheque", "bank_transfer", "upi_qr", "razorpay"]);

function ReceiptSheet({
  receipt,
  heading,
  t,
  flash,
}: {
  receipt: Receipt;
  heading: string;
  t: T;
  flash: PageNotice | null;
}) {
  const mode = MODES.has(receipt.mode) ? receipt.mode : "other";
  return (
    <section className="public-card" aria-labelledby="public-heading">
      <h1 id="public-heading">{heading}</h1>
      {receipt.cancelled_on ? (
        <Banner tone="red" icon="cancelled">
          {t("receipt.cancelled", { date: parentDay(receipt.cancelled_on) })}
        </Banner>
      ) : null}
      <dl className="public-facts">
        <div>
          <dt>{t("receipt.number")}</dt>
          <dd className="public-figure">{receipt.receipt_number}</dd>
        </div>
        <div>
          <dt>{t("receipt.date")}</dt>
          <dd className="public-figure">{parentDay(receipt.receipt_date)}</dd>
        </div>
        <div>
          <dt>{t("receipt.child")}</dt>
          <dd>{receipt.student_name}</dd>
        </div>
        <div>
          <dt>{t("receipt.class")}</dt>
          <dd>{receipt.class_section}</dd>
        </div>
        <div>
          <dt>{t("receipt.admissionNo")}</dt>
          <dd className="public-figure">{receipt.admission_no}</dd>
        </div>
        <div>
          <dt>{t("receipt.mode")}</dt>
          <dd>{t(`receipt.modes.${mode}` as "receipt.modes.cash")}</dd>
        </div>
      </dl>
      <table className="public-table">
        <thead>
          <tr>
            <th scope="col">{t("receipt.item")}</th>
            <th scope="col" className="public-num">
              {t("receipt.amount")}
            </th>
          </tr>
        </thead>
        <tbody>
          {receipt.lines.map((line, i) => (
            <tr key={i}>
              <td>
                {line.head_name}
                <span className="public-muted"> · {line.label}</span>
              </td>
              <td className="public-num public-figure">{parentRupees(line.amount)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">{t("receipt.total")}</th>
            <td className="public-num public-figure">{parentRupees(receipt.amount)}</td>
          </tr>
        </tfoot>
      </table>
      {receipt.amount_in_words ? (
        <p className="public-muted">{t("receipt.inWords", { words: receipt.amount_in_words })}</p>
      ) : null}
      {flash?.kind === "pdf" ? (
        <Banner tone="amber" icon="alert">
          {t("common.pdfUnavailable")}
        </Banner>
      ) : null}
      {receipt.has_pdf ? (
        // eslint-disable-next-line @next/next/no-html-link-for-pages -- a plain link (no client router)
        <a className="public-button" href="/p/document">
          {t("receipt.savePdf")}
        </a>
      ) : receipt.cancelled_on ? null : (
        <p className="public-muted">{t("common.pdfNotReady")}</p>
      )}
      <p className="public-note">{t("receipt.keep")}</p>
    </section>
  );
}

// --- dues --------------------------------------------------------------------------------------

function DueRow({ due, t }: { due: Dues["instalments"][number]; t: T }) {
  return (
    <>
      <span className="public-due-label">
        {due.label}
        {due.overdue ? <span className="public-tag">{t("dues.overdue")}</span> : null}
      </span>
      <span className="public-muted">
        {due.due_on ? t("dues.dueOn", { date: parentDay(due.due_on) }) : t("dues.noDate")}
        {Number(due.late_fee) > 0
          ? ` · ${t("dues.lateFee", { amount: parentRupees(due.late_fee) })}`
          : ""}
      </span>
      <span className="public-due-amount public-figure">{parentRupees(due.due)}</span>
    </>
  );
}

function DuesSheet({
  dues,
  csrf,
  t,
  flash,
}: {
  dues: Dues;
  csrf: string;
  t: T;
  flash: PageNotice | null;
}) {
  const heading = dues.child_first_name
    ? t("dues.title", { name: dues.child_first_name })
    : t("dues.titleNoName");
  const many = dues.instalments.length > 1;
  return (
    <section className="public-card" aria-labelledby="public-heading">
      <h1 id="public-heading">{heading}</h1>
      <p className="public-muted">
        {dues.class_name ? `${dues.class_name} · ` : ""}
        {t("dues.asOf", { date: parentDay(dues.as_of) })}
      </p>
      {flash?.kind === "pay" ? (
        <Banner tone="red" icon="alert">
          {t(`dues.errors.${flash.error}`)}
        </Banner>
      ) : null}
      {dues.instalments.length === 0 ? (
        <Banner tone="green" icon="valid">
          {t("dues.nothingDue")}
        </Banner>
      ) : dues.pay_online ? (
        <form className="public-form" method="post" action="/p/pay" id="pay">
          <Csrf value={csrf} />
          <fieldset className="public-dues">
            <legend className={many ? undefined : "public-visually-hidden"}>
              {t("dues.choose")}
            </legend>
            {dues.instalments.map((due) =>
              many ? (
                <label key={due.instalment_no} className="public-due public-due-check">
                  <input
                    type="checkbox"
                    name="instalment"
                    value={due.instalment_no}
                    defaultChecked
                  />
                  <DueRow due={due} t={t} />
                </label>
              ) : (
                <div key={due.instalment_no} className="public-due">
                  <input type="hidden" name="instalment" value={due.instalment_no} />
                  <DueRow due={due} t={t} />
                </div>
              ),
            )}
          </fieldset>
          <Total dues={dues} t={t} />
          <button type="submit" className="public-button">
            {many
              ? t("dues.payOnlineChosen")
              : t("dues.payOnline", { amount: parentRupees(dues.total_due) })}
          </button>
          <p className="public-muted">{t("dues.payOnlineNote")}</p>
        </form>
      ) : (
        <>
          <ul className="public-dues">
            {dues.instalments.map((due) => (
              <li key={due.instalment_no} className="public-due">
                <DueRow due={due} t={t} />
              </li>
            ))}
          </ul>
          <Total dues={dues} t={t} />
        </>
      )}
      {dues.upi && dues.instalments.length > 0 ? (
        <details className="public-details" open={!dues.pay_online}>
          <summary>{t("dues.upiTitle")}</summary>
          {/* eslint-disable-next-line @next/next/no-img-element -- an inline data: SVG, nothing to optimise */}
          <img
            className="public-qr"
            src={`data:image/svg+xml;base64,${Buffer.from(dues.upi.qr_svg).toString("base64")}`}
            alt={t("dues.upiQrAlt", { payee: dues.upi.payee_name })}
            width={220}
            height={220}
          />
          <p>{t("dues.upiPayee", { payee: dues.upi.payee_name })}</p>
          <p className="public-code">{t("dues.upiId", { id: dues.upi.upi_id })}</p>
          <p className="public-muted">{t("dues.upiHow")}</p>
        </details>
      ) : null}
      <p className="public-note">
        {t("dues.paidAlready")}
        {dues.office_phone ? (
          <>
            {" "}
            <a href={`tel:${dues.office_phone.replace(/[^\d+]/g, "")}`}>
              {t("dues.office", { phone: dues.office_phone })}
            </a>
          </>
        ) : null}
      </p>
      <p className="public-muted">{t("dues.noCard")}</p>
    </section>
  );
}

function Total({ dues, t }: { dues: Dues; t: T }) {
  return (
    <p className="public-total">
      <span>{t("dues.total")}</span>
      <span className="public-figure">{parentRupees(dues.total_due)}</span>
      {Number(dues.late_fee_total) > 0 ? (
        <span className="public-muted public-total-note">
          {t("dues.lateTotal", { amount: parentRupees(dues.late_fee_total) })}
        </span>
      ) : null}
    </p>
  );
}

// --- opt-in ------------------------------------------------------------------------------------

function OptinSheet({
  optin,
  school,
  csrf,
  t,
  flash,
}: {
  optin: Optin;
  school: string;
  csrf: string;
  t: T;
  flash: PageNotice | null;
}) {
  const stopped = optin.categories.some((c) => c.stopped_by_parent);
  const schoolLanguage = t(`optin.languageNames.${optin.school_language}`);
  return (
    <section className="public-card" aria-labelledby="public-heading">
      <h1 id="public-heading">{t("optin.title")}</h1>
      <p>{t("optin.intro", { school })}</p>
      {flash?.kind === "saved" ? (
        <Banner tone={flash.ok ? "green" : "red"} icon={flash.ok ? "valid" : "alert"}>
          {flash.ok ? t("optin.saved") : t("optin.saveFailed")}
        </Banner>
      ) : null}
      <dl className="public-facts">
        {optin.categories.map((c) => (
          <div key={c.category}>
            <dt>{t(`optin.categories.${c.category}`)}</dt>
            <dd>{t(`optin.status.${c.status}`)}</dd>
          </div>
        ))}
      </dl>
      {stopped ? <p className="public-muted">{t("optin.stoppedByYou")}</p> : null}
      <p className="public-muted">{t("optin.howToJoin")}</p>
      <form className="public-form" method="post" action="/p/optin">
        <Csrf value={csrf} />
        <fieldset className="public-choices">
          <legend>{t("optin.languageTitle")}</legend>
          {(
            [
              ["en", t("optin.languageEn")],
              ["te", t("optin.languageTe")],
              ["school", t("optin.languageSchool", { language: schoolLanguage })],
            ] as const
          ).map(([value, label]) => (
            <label key={value} className="public-choice">
              <input
                type="radio"
                name="language"
                value={value}
                defaultChecked={optin.language_choice === value}
              />
              <span lang={value === "te" ? "te" : undefined}>{label}</span>
            </label>
          ))}
        </fieldset>
        <button type="submit" className="public-button">
          {t("optin.save")}
        </button>
      </form>
    </section>
  );
}

// --- the page ----------------------------------------------------------------------------------

export function LinkFrame({
  envelope,
  csrf = "",
  notice = null,
}: {
  envelope: LinkEnvelope;
  csrf?: string;
  notice?: PageNotice | null;
}) {
  const t = parentTranslator(envelope.language);
  const heading = publicTranslator(envelope.language)(`kind.${envelope.kind}`);
  let sheet: React.ReactNode = null;
  if (envelope.kind === "parent_notice" && envelope.notice) {
    sheet = <NoticeSheet notice={envelope.notice} t={t} flash={notice} />;
  } else if (envelope.kind === "parent_receipt" && envelope.receipt) {
    sheet = <ReceiptSheet receipt={envelope.receipt} heading={heading} t={t} flash={notice} />;
  } else if (envelope.kind === "parent_dues" && envelope.dues) {
    sheet = <DuesSheet dues={envelope.dues} csrf={csrf} t={t} flash={notice} />;
  } else if (envelope.kind === "parent_optin" && envelope.optin) {
    sheet = (
      <OptinSheet
        optin={envelope.optin}
        school={envelope.school_name}
        csrf={csrf}
        t={t}
        flash={notice}
      />
    );
  }
  return <Frame envelope={envelope}>{sheet}</Frame>;
}

/**
 * Razorpay's return page (`/p/paid`): the status read through the dues link. While Razorpay has
 * not confirmed, the page reloads itself every 5 seconds (a meta refresh, no JavaScript) up to
 * `maxChecks` times, then offers "Check again".
 */
export function PaidPage({
  envelope,
  payment,
  refValue,
  checks,
  maxChecks,
}: {
  envelope: LinkEnvelope;
  payment: Payment | null;
  refValue: string | null;
  checks: number;
  maxChecks: number;
}) {
  const t = parentTranslator(envelope.language);
  const ref = refValue ? `ref=${refValue}&` : "";
  let body: React.ReactNode;
  if (payment === null) {
    body = <p>{t("paid.unknown")}</p>;
  } else if (payment.status === "paid") {
    body = (
      <>
        <Banner tone="green" icon="valid">
          {t("paid.thanks", { amount: parentRupees(payment.amount) })}
        </Banner>
        <p>{payment.receipt_issued ? t("paid.receiptReady") : t("paid.receipt")}</p>
      </>
    );
  } else if (payment.status === "created") {
    body =
      checks < maxChecks ? (
        <>
          <meta httpEquiv="refresh" content={`5;url=/p/paid?${ref}n=${checks + 1}`} />
          <Banner tone="amber" icon="clock">
            {t("paid.waiting", { amount: parentRupees(payment.amount) })}
          </Banner>
          <p className="public-muted">{t("paid.waitingNote")}</p>
        </>
      ) : (
        <>
          <Banner tone="amber" icon="clock">
            {t("paid.stillWaiting")}
          </Banner>
          <a className="public-button" href={`/p/paid?${ref}n=0`}>
            {t("paid.checkAgain")}
          </a>
        </>
      );
  } else {
    body = (
      <Banner tone="red" icon="cancelled">
        {t("paid.notPaid")}
      </Banner>
    );
  }
  return (
    <Frame envelope={envelope}>
      <section className="public-card" aria-labelledby="public-heading">
        <h1 id="public-heading">{t("paid.title")}</h1>
        {body}
        <p className="public-note">
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- a plain link (no client router) */}
          <a href="/p/view">{t("paid.backToDues")}</a>
        </p>
      </section>
    </Frame>
  );
}

/**
 * The one page for every link that does not open (unknown, expired, revoked, suspended school):
 * no school name, the same words for all (ADR-0046 decision 7; docs/17 §5.8). English, because
 * nothing says which school or language the link belonged to.
 */
export function ExpiredLink() {
  return <Notice variant="expired" />;
}

/** Too many tries from this address, or the API is unavailable: try again later. */
export function BusyLink() {
  return <Notice variant="busy" />;
}

function Notice({ variant }: { variant: "expired" | "busy" }) {
  const t = publicTranslator("en");
  return (
    <main className="public-page" lang="en">
      <title>{t("meta.title")}</title>
      <section className="public-card" aria-labelledby="public-heading">
        <p className="public-muted">
          <PublicIcon name={variant === "expired" ? "unlinked" : "clock"} />
        </p>
        <h1 id="public-heading">{t(`${variant}.title`)}</h1>
        <p>{t(`${variant}.body`)}</p>
      </section>
    </main>
  );
}
