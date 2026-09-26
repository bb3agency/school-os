import { useTranslations } from "next-intl";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { Loadable } from "@/lib/loadable";
import { Alert } from "./Alert";
import { EmptyState } from "./EmptyState";
import { LoadingState } from "./LoadingState";

export function Table({ className, ...props }: ComponentProps<"table">) {
  return <table className={cn("w-full border-collapse text-left text-sm", className)} {...props} />;
}

export function THead(props: ComponentProps<"thead">) {
  return <thead className="bg-surface-muted" {...props} />;
}

export function TBody(props: ComponentProps<"tbody">) {
  return <tbody className="divide-y divide-border" {...props} />;
}

export function Tr(props: ComponentProps<"tr">) {
  return <tr {...props} />;
}

export function Th({ className, scope = "col", ...props }: ComponentProps<"th">) {
  return (
    <th
      scope={scope}
      className={cn("px-3 py-2 font-semibold whitespace-nowrap text-ink", className)}
      {...props}
    />
  );
}

export function Td({ className, ...props }: ComponentProps<"td">) {
  return <td className={cn("px-3 py-2 align-top text-ink", className)} {...props} />;
}

export interface Column<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  className?: string;
}

export interface DataTableProps<T> {
  /** Visible caption; also names the scrollable region. */
  caption: string;
  columns: ReadonlyArray<Column<T>>;
  state: Loadable<readonly T[]>;
  rowKey: (row: T) => string;
  emptyTitle: ReactNode;
  emptyBody?: ReactNode;
  emptyAction?: ReactNode;
  /** Hide the caption visually when a heading right above already says the same. */
  captionHidden?: boolean;
}

/**
 * Table with loading, error and empty states. Wide tables scroll inside a focusable
 * region so keyboard users can scroll them at 1366×768.
 */
export function DataTable<T>({
  caption,
  columns,
  state,
  rowKey,
  emptyTitle,
  emptyBody,
  emptyAction,
  captionHidden = false,
}: DataTableProps<T>) {
  const t = useTranslations("common");

  if (state.status === "loading") return <LoadingState label={t("loading")} />;
  if (state.status === "error") {
    return (
      <Alert tone="danger" title={t("loadErrorTitle")}>
        {t("loadErrorBody")}
      </Alert>
    );
  }
  if (state.status === "unavailable") {
    return <EmptyState title={t("notAvailableYetTitle")} body={t("notAvailableYetBody")} />;
  }
  if (state.data.length === 0) {
    return <EmptyState title={emptyTitle} body={emptyBody} action={emptyAction} />;
  }

  return (
    <div
      role="region"
      aria-label={t("scrollableTable", { caption })}
      tabIndex={0}
      className="overflow-x-auto rounded-md border border-border print:overflow-visible print:border-0"
    >
      <Table>
        <caption className={cn("px-3 py-2 text-left font-semibold", captionHidden && "sr-only")}>
          {caption}
        </caption>
        <THead>
          <Tr>
            {columns.map((column) => (
              <Th key={column.key} className={column.className}>
                {column.header}
              </Th>
            ))}
          </Tr>
        </THead>
        <TBody>
          {state.data.map((row) => (
            <Tr key={rowKey(row)}>
              {columns.map((column) => (
                <Td key={column.key} className={column.className}>
                  {column.cell(row)}
                </Td>
              ))}
            </Tr>
          ))}
        </TBody>
      </Table>
    </div>
  );
}
