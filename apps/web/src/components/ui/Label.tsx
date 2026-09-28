import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

export function Label({ className, ...props }: ComponentProps<"label">) {
  // eslint-disable-next-line jsx-a11y/label-has-associated-control -- callers pass htmlFor
  return <label className={cn("block text-sm font-medium text-ink", className)} {...props} />;
}
