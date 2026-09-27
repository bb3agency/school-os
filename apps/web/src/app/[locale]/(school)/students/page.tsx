import { StudentsScreen } from "@/features/students/StudentList";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("students.list.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const first = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;

/** US-302 / FR-STU-010: GET /students through the BFF, filtered by the URL (GET form). */
export default async function StudentsPage({ searchParams }: Props) {
  const params = await searchParams;
  const filters = {
    q: first(params.q),
    classId: first(params.class_id),
    sectionId: first(params.section_id),
    status: first(params.status),
  };
  // A new search starts again at page 1 (the key resets the cursor history).
  return <StudentsScreen key={JSON.stringify(filters)} filters={filters} />;
}
