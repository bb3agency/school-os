import { RegistersScreen } from "@/features/certificates/RegistersScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("certificates.registers.title"));

/** US-1106, FR-REG-001..004: print views of the TC, certificate and admission registers. */
export default function RegistersPage() {
  return <RegistersScreen />;
}
