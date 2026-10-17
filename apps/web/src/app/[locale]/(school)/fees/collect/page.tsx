import { CounterScreen } from "@/features/fees/CounterScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("fees.counter.title"));

type Props = { searchParams: Promise<{ account?: string | string[] }> };

/** US-2102, FR-FEE-010: the fee counter (keyboard-only; docs/17 §5.8). `account` is an id only. */
export default async function FeeCounterPage({ searchParams }: Props) {
  const { account } = await searchParams;
  const id = typeof account === "string" && UUID_PATTERN.test(account) ? account : null;
  return <CounterScreen initialAccountId={id} />;
}
