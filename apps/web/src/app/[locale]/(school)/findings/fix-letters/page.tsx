import { FixLettersScreen } from "@/features/fix-letters/FixLettersScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fixLetters.title"));

/** FA-08, FR-DQ-049..060: letters to parents and the board office from readiness differences. */
export default function FixLettersPage() {
  return <FixLettersScreen />;
}
