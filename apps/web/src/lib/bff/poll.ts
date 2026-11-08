import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { PASSIVE_HEADER } from "./fetch";

/**
 * Headers for a status poll's GET (XB-06; FR-IAM-003, SEC-006). The first load of a query is
 * the person's own request and counts as activity. Every later fetch of data already on the
 * screen (the automatic `refetchInterval` polls while a job is queued or running) is passive:
 * the BFF reads the session without sliding the idle timeout, so a job that never finishes
 * does not keep an unattended office PC signed in. Pass the query function's context.
 */
export function pollHeaders(context: {
  client: QueryClient;
  queryKey: QueryKey;
}): Record<string, string> {
  const shown = (context.client.getQueryState(context.queryKey)?.dataUpdatedAt ?? 0) > 0;
  return shown ? { [PASSIVE_HEADER]: "1" } : {};
}
