import { TallyVouchersScreen } from "@/features/tally/TallyVouchersScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("tally.vouchers.title"));

/**
 * FM-03, FR-TALLY-011..030: the Tally ledger mapping, the voucher file for closed days, earlier
 * files and the ledger-vs-Tally check. Behind the school's fees switch; SchoolOS never writes
 * to Tally (the accountant imports the file by hand).
 */
export default function TallyVouchersPage() {
  return <TallyVouchersScreen />;
}
