import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";

/**
 * Filter bar: GET form with the fields, "Apply filters" and, when filtered, "Clear filters".
 * `bare` drops the card, for the toolbar of a `DataTableV2` (docs/17 §4.3), which is already
 * inside the table's card; without it the filters sit in their own card above several tables.
 */
export function FilterCard({
  children,
  clearHref,
  bare = false,
}: {
  children: ReactNode;
  /** Unfiltered URL; the "Clear filters" link shows only when a filter is set. */
  clearHref?: string | undefined;
  bare?: boolean;
}) {
  const tc = useTranslations("common");
  const t = useTranslations("platform.schools");
  const form = (
    // One column on phones (docs/17 §5.7): every filter takes the full width.
    <form method="get" className="flex flex-wrap items-end gap-3 max-sm:[&>*]:w-full">
      {children}
      <div className="relative flex flex-wrap gap-2">
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
  );
  return bare ? form : <Card padding="sm">{form}</Card>;
}
