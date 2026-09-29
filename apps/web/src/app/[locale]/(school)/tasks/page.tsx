import { TasksScreen } from "@/features/circulars/TasksScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("tasks.title"));

/** US-1603, US-1604: my tasks and (task.read_all) the school's tasks. */
export default function TasksPage() {
  return <TasksScreen />;
}
