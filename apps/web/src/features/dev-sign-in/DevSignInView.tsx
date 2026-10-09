import { useTranslations } from "next-intl";
import { MinimalShell } from "@/components/shell/MinimalShell";
import { Alert } from "@/components/ui/Alert";
import { buttonClasses } from "@/components/ui/Button";
import { cardClasses } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { Table, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { cn } from "@/lib/cn";
import { translateOr } from "@/lib/i18n-dynamic";
import { CopyButton } from "./CopyButton";
import type { DevSchool } from "./staff";

/** Staff sign-in always goes through the normal BFF flow and the local stub's login page. */
const SIGN_IN = "/bff/auth/login";

/** Local-development sign-in helper (rendered only when `isDevSignInEnabled()`). */
export function DevSignInView({ schools }: { schools: DevSchool[] }) {
  const t = useTranslations("devSignIn");
  const tAll = useTranslations();
  // Role keys come from the seed plan; fall back to the column heading if one is missing.
  const tr = (role: string) =>
    translateOr(tAll, `school.users.roles.${role}`, "devSignIn.columns.role");
  return (
    <MinimalShell wide>
      <div className="space-y-6">
        <PageHeader title={t("title")} plain />
        <Alert tone="warning">{t("localOnly")}</Alert>
        <div className={cn(cardClasses(), "space-y-3")}>
          <ol className="list-decimal space-y-1 pl-6 text-ink">
            <li>{t("steps.copy")}</li>
            <li>{t("steps.signIn")}</li>
            <li>{t("steps.paste")}</li>
          </ol>
          <p className="text-ink-muted">{t("mfaNote")}</p>
        </div>
        {schools.map((school) => (
          <section
            key={school.code}
            aria-labelledby={`school-${school.code}`}
            className={cn(cardClasses(), "space-y-3")}
          >
            <h2 id={`school-${school.code}`} className="text-lg font-semibold">
              {t("school", { code: school.code })}
            </h2>
            <div className="overflow-x-auto rounded-lg border border-border">
              <Table>
                <caption className="sr-only">{t("tableCaption", { code: school.code })}</caption>
                <THead>
                  <Tr>
                    <Th>{t("columns.role")}</Th>
                    <Th>{t("columns.subject")}</Th>
                    <Th>
                      <span className="sr-only">{t("columns.actions")}</span>
                    </Th>
                  </Tr>
                </THead>
                <TBody>
                  {school.staff.map((person) => (
                    <Tr key={person.subject}>
                      <Td>
                        {tr(person.role)}
                        {person.count > 1 ? (
                          <span className="block text-xs text-ink-muted">
                            {t("more", { count: person.count })}
                          </span>
                        ) : null}
                      </Td>
                      <Td>
                        <code className="font-mono text-xs break-all select-all">
                          {person.subject}
                        </code>
                      </Td>
                      <Td className="whitespace-nowrap">
                        <span className="flex flex-wrap items-center gap-2">
                          <CopyButton text={person.subject} label={t("copy")} />
                          <a
                            href={SIGN_IN}
                            className={buttonClasses("primary", "sm")}
                            aria-label={t("signInAs", { role: tr(person.role), code: school.code })}
                          >
                            {t("signIn")}
                          </a>
                        </span>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </div>
          </section>
        ))}
        <section aria-labelledby="dev-step-up" className={cn(cardClasses(), "space-y-2")}>
          <h2 id="dev-step-up" className="text-lg font-semibold">
            {t("stepUp.title")}
          </h2>
          <p className="text-ink">{t("stepUp.body")}</p>
          <CopyButton stepUpClaims label={t("stepUp.copy")} />
        </section>
        <section aria-labelledby="dev-operators" className={cn(cardClasses(), "space-y-2")}>
          <h2 id="dev-operators" className="text-lg font-semibold">
            {t("operators.title")}
          </h2>
          <p className="text-ink">{t("operators.body")}</p>
        </section>
      </div>
    </MinimalShell>
  );
}
