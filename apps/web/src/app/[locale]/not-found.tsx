import { useTranslations } from "next-intl";
import { ButtonLink } from "@/components/ui/Button";

export default function NotFound() {
  const t = useTranslations("errors");
  return (
    <main id="main" className="mx-auto max-w-xl space-y-4 p-10 text-center">
      <h1 className="text-2xl font-semibold">{t("notFoundTitle")}</h1>
      <p className="text-ink-muted">{t("notFoundBody")}</p>
      <ButtonLink href="/">{t("goHome")}</ButtonLink>
    </main>
  );
}
