"use client";

import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/Button";
import type { PagedList } from "./data";

/** "Show more" under a cursor-paged operator list; nothing when every page is loaded (R-14). */
export function ShowMore({
  list,
}: {
  list: Pick<PagedList<unknown>, "hasMore" | "loadingMore" | "loadMore">;
}) {
  const t = useTranslations("platform");
  const tc = useTranslations("common");
  if (!list.hasMore) return null;
  return (
    <div className="flex justify-center">
      <Button variant="secondary" onClick={list.loadMore} disabled={list.loadingMore}>
        {list.loadingMore ? tc("loading") : t("showMore")}
      </Button>
    </div>
  );
}
