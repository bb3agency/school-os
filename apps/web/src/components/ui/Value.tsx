import { useTranslations } from "next-intl";

/** Shows a formatted value, or "—" (read out as "Not available") when it is missing. */
export function Value({ children }: { children: string | null | undefined }) {
  const t = useTranslations("common");
  if (children !== null && children !== undefined && children !== "") return <>{children}</>;
  return (
    <>
      <span aria-hidden="true">—</span>
      <span className="sr-only">{t("notAvailable")}</span>
    </>
  );
}
