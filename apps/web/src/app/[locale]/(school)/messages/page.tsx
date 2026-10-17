import { MessagesScreen } from "@/features/messaging/MessagesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("messaging.logTitle"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function id(value: string | string[] | undefined): string | undefined {
  return typeof value === "string" && UUID.test(value) ? value : undefined;
}

/** US-2208, FR-MSG-017: message history and delivery log (ids only in the URL). */
export default async function MessagesPage({ searchParams }: Props) {
  const params = await searchParams;
  return <MessagesScreen studentId={id(params.student_id)} guardianId={id(params.guardian_id)} />;
}
