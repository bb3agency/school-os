import { useTranslations } from "next-intl";
import { MinimalShell } from "@/components/shell/MinimalShell";
import { ButtonLink } from "@/components/ui/Button";
import { Illustration } from "@/components/ui/Illustration";

/**
 * The localised "page not found" (also every unknown path under a locale, `[...rest]`): what
 * happened and the one way on, in the minimal shell, with a small decorative drawing of the
 * records almirah.
 */
export default function NotFound() {
  const t = useTranslations("errors");
  return (
    <MinimalShell>
      <div className="mx-auto flex max-w-xl flex-col items-center gap-4 text-center">
        <Illustration name="records-almirah" className="w-56 max-w-full" />
        <h1 className="text-2xl font-semibold text-ink">{t("notFoundTitle")}</h1>
        <p className="text-ink-muted">{t("notFoundBody")}</p>
        <ButtonLink href="/">{t("goHome")}</ButtonLink>
      </div>
    </MinimalShell>
  );
}
