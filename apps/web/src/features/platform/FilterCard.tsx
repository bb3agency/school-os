import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";

/** Filter bar card: GET form with the fields, "Apply filters" and, when filtered, "Clear". */
export function FilterCard({
  children,
  clearHref,
}: {
  children: ReactNode;
  /** Unfiltered URL; the "Clear filters" link shows only when a filter is set. */
  clearHref?: string | undefined;
}) {
  const tc = useTranslations("common");
  const t = useTranslations("platform.schools");
  return (
    <Card padding="sm">
      <form method="get" className="flex flex-wrap items-end gap-3">
        {children}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="secondary">
            <Icon name="filter" className="size-4" />
            {tc("applyFilters")}
          </Button>
          {clearHref ? (
            <ButtonLink href={clearHref} variant="ghost">
              {t("clearFilters")}
            </ButtonLink>
          ) : null}
        </div>
      </form>
    </Card>
  );
}
