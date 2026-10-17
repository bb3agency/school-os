import {
  parentDate,
  publicTranslator,
  PUBLIC_TELUGU_STYLESHEET_HREF,
  type ParentLanguage,
} from "@/server/public/messages";

/**
 * Parent page shells (ADR-0046; docs/17 §5.8). Server components without client JavaScript:
 * the school's name at the top, "From <school>, sent through SchoolOS" and the forwarding
 * warning at the bottom, the text in the parent language. The page content per kind (notice,
 * receipt, dues, opt-in) is added by wave R2-3H.
 */

export interface LinkEnvelope {
  kind: "parent_notice" | "parent_receipt" | "parent_dues" | "parent_optin";
  expires_at: string;
  language: ParentLanguage;
  school_name: string;
}

/** The self-hosted Telugu font, only on Telugu pages (ADR-0044 decision 3). */
function TeluguFont({ language }: { language: ParentLanguage }) {
  if (language !== "te") return null;
  return <link rel="stylesheet" href={PUBLIC_TELUGU_STYLESHEET_HREF} precedence="default" />;
}

export function LinkFrame({ envelope }: { envelope: LinkEnvelope }) {
  const { language } = envelope;
  const t = publicTranslator(language);
  return (
    <main className="public-page" lang={language}>
      <TeluguFont language={language} />
      <title>{t("meta.title")}</title>
      <p className="public-school">{envelope.school_name}</p>
      <section className="public-card" aria-labelledby="public-heading">
        <h1 id="public-heading">{t(`kind.${envelope.kind}`)}</h1>
        <p className="public-muted">{t("view.detailsSoon")}</p>
        <p>{t("view.validUntil", { date: parentDate(envelope.expires_at, language) })}</p>
      </section>
      <footer className="public-footer">
        <p>{t("footer.fromSchool", { school: envelope.school_name })}</p>
        <p>{t("footer.doNotForward")}</p>
      </footer>
    </main>
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
        <h1 id="public-heading">{t(`${variant}.title`)}</h1>
        <p>{t(`${variant}.body`)}</p>
      </section>
    </main>
  );
}
