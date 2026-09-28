import { useTranslations } from "next-intl";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { Loadable } from "@/lib/loadable";
import { Alert } from "./Alert";
import { EmptyState } from "./EmptyState";
import { LoadingState } from "./LoadingState";

/**
 * `comfortable` (default): rows about 56px high, like the reference tables.
 * `compact`: dense registers and long lists.
 */
export type TableDensity = "comfortable" | "compact";

export interface TableProps extends ComponentProps<"table"> {
  density?: TableDensity;
}

/**
 * Plain table: light header row, thin row dividers, row hover, no heavy grid. Cells read
 * the density from `data-density` (no React context, so it works in server components).
 */
export function Table({ className, density = "comfortable", ...props }: TableProps) {
  return (
    <table
      data-density={density}
      className={cn("group/table w-full border-collapse text-left text-sm", className)}
      {...props}
    />
  );
}

export function THead({ className, ...props }: ComponentProps<"thead">) {
  return (
    <thead
      className={cn("border-b border-border bg-surface-muted print:bg-white", className)}
      {...props}
    />
  );
}

export function TBody({ className, ...props }: ComponentProps<"tbody">) {
  return (
    <tbody
      className={cn(
        "divide-y divide-border [&>tr]:transition-colors [&>tr:hover]:bg-surface-hover",
        className,
      )}
      {...props}
    />
  );
}

export function Tr(props: ComponentProps<"tr">) {
  return <tr {...props} />;
}

export function Th({ className, scope = "col", ...props }: ComponentProps<"th">) {
  return (
    <th
      scope={scope}
      className={cn(
        "px-4 py-3 text-xs font-medium whitespace-nowrap text-ink-muted",
        "group-data-[density=compact]/table:px-3 group-data-[density=compact]/table:py-2",
        className,
      )}
      {...props}
    />
  );
}

export function Td({ className, ...props }: ComponentProps<"td">) {
  return (
    <td
      className={cn(
        "px-4 py-4 align-middle text-ink",
        "group-data-[density=compact]/table:px-3 group-data-[density=compact]/table:py-2 group-data-[density=compact]/table:align-top",
        className,
      )}
      {...props}
    />
  );
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
  density?: TableDensity;
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
  density = "comfortable",
}: DataTableProps<T>) {
  const t = useTranslations("common");
  const te = useTranslations("errors");

  if (state.status === "loading") return <LoadingState label={t("loading")} />;
  if (state.status === "error") {
    return (
      <Alert tone="danger" title={t("loadErrorTitle")}>
        {state.reason ? te(`load.${state.reason}`) : t("loadErrorBody")}
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
      className="overflow-x-auto rounded-xl border border-border bg-surface shadow-card print:overflow-visible print:border-0 print:shadow-none"
    >
      <Table density={density}>
        <caption
          className={cn("px-4 py-3 text-left font-medium text-ink", captionHidden && "sr-only")}
        >
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
