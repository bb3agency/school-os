"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
} from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError, unwrap, useBffClient } from "@/lib/bff/query";
import { ifMatch } from "./data";
import type {
  ConversationDetail,
  ConversationMessage,
  ConversationPage,
  ConversationPatch,
  ConversationSummary,
} from "./data";

/**
 * Ask conversations (FR-KB-012): the list (sidebar recents and the history page share one
 * cached query), one conversation's messages, and rename / pin / delete with optimistic
 * updates that roll back when the API refuses (docs/17 §5.3).
 */

export const CONVERSATION_KEYS = {
  all: ["staff", "knowledge", "conversations"] as const,
  list: ["staff", "knowledge", "conversations", "list"] as const,
  detail: (id: string) => ["staff", "knowledge", "conversations", "detail", id] as const,
};

/** Page size of the list (the sidebar shows the first RECENT_COUNT). */
export const PAGE_SIZE = 20;
export const RECENT_COUNT = 8;

type ListData = InfiniteData<ConversationPage, string | undefined>;

const retry = (count: number, error: unknown) =>
  count < 1 &&
  !(error instanceof NotAvailableError) &&
  !(error instanceof AuthRedirectError) &&
  !(error instanceof ApiError && error.status < 500);

/** Pinned first, then the newest activity (the API's order; kept after local changes). */
export function sortConversations(items: readonly ConversationSummary[]): ConversationSummary[] {
  return [...items].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return b.updated_at.localeCompare(a.updated_at);
  });
}

function flatten(data: { pages: readonly ConversationPage[] } | undefined): ConversationSummary[] {
  const seen = new Set<string>();
  const out: ConversationSummary[] = [];
  for (const page of data?.pages ?? []) {
    for (const item of page.data) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      out.push(item);
    }
  }
  return sortConversations(out);
}

export interface ConversationList {
  items: ConversationSummary[];
  isPending: boolean;
  isError: boolean;
  error: unknown;
  hasMore: boolean;
  loadingMore: boolean;
  loadMore: () => void;
  refetch: () => void;
}

/** GET /knowledge/conversations with cursor paging (one cache for sidebar and history). */
export function useConversationList(enabled = true): ConversationList {
  const api = useBffClient("staff");
  const query = useInfiniteQuery({
    queryKey: CONVERSATION_KEYS.list,
    queryFn: ({ pageParam }): Promise<ConversationPage> =>
      unwrap(
        api.GET("/api/v1/knowledge/conversations", {
          params: { query: { limit: PAGE_SIZE, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled,
    retry,
    staleTime: 30_000,
  });
  const items = useMemo(() => flatten(query.data), [query.data]);
  const { fetchNextPage, hasNextPage, isFetchingNextPage, refetch } = query;
  const loadMore = useCallback(() => {
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage]);
  return {
    items,
    isPending: query.isPending && enabled,
    isError: query.isError,
    error: query.error,
    hasMore: Boolean(hasNextPage),
    loadingMore: isFetchingNextPage,
    loadMore,
    refetch: () => void refetch(),
  };
}

/** How often a conversation with an answer still being written is read again. */
export const STREAMING_POLL_MS = 10_000;
/** A message `streaming` for longer than this has stopped (its stream ended unrecorded). */
export const STREAMING_STALE_MS = 5 * 60_000;

/**
 * An answer being written elsewhere (another window or device): a current message that is
 * still `streaming` and recent. An older one ended without being recorded; it stays as is.
 */
export function writingElsewhere(
  messages: readonly ConversationMessage[],
  now: number = Date.now(),
): boolean {
  return messages.some(
    (m) =>
      m.status === "streaming" &&
      !m.superseded &&
      now - Date.parse(m.created_at) < STREAMING_STALE_MS,
  );
}

/**
 * GET /knowledge/conversations/{id}. While one of its answers is being written elsewhere it is
 * read again every STREAMING_POLL_MS, so the answer appears when it is ready.
 */
export function useConversation(id: string | null) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: CONVERSATION_KEYS.detail(id ?? "none"),
    queryFn: (): Promise<ConversationDetail> =>
      unwrap(
        api.GET("/api/v1/knowledge/conversations/{conversation_id}", {
          params: { path: { conversation_id: id ?? "" } },
        }),
      ),
    enabled: id !== null,
    retry,
    staleTime: 60_000,
    refetchInterval: (query) =>
      query.state.data && writingElsewhere(query.state.data.messages) ? STREAMING_POLL_MS : false,
  });
}

// --- cache helpers ---------------------------------------------------------------------------

function mapList(
  client: QueryClient,
  change: (items: ConversationSummary[]) => ConversationSummary[],
): void {
  client.setQueryData<ListData>(CONVERSATION_KEYS.list, (data) => {
    if (!data) return data;
    // Maps and filters apply page by page (upsertConversation adds new items).
    return { ...data, pages: data.pages.map((page) => ({ ...page, data: change(page.data) })) };
  });
}

/** Insert or update one conversation in the cached list (e.g. from an answer's `meta`). */
export function upsertConversation(client: QueryClient, summary: ConversationSummary): void {
  const data = client.getQueryData<ListData>(CONVERSATION_KEYS.list);
  if (!data) {
    client.setQueryData<ListData>(CONVERSATION_KEYS.list, {
      pages: [{ data: [summary], next_cursor: null }],
      pageParams: [undefined],
    });
    return;
  }
  const exists = data.pages.some((page) => page.data.some((item) => item.id === summary.id));
  client.setQueryData<ListData>(CONVERSATION_KEYS.list, {
    ...data,
    pages: data.pages.map((page, i) => ({
      ...page,
      data: exists
        ? page.data.map((item) => (item.id === summary.id ? { ...item, ...summary } : item))
        : i === 0
          ? [summary, ...page.data]
          : page.data,
    })),
  });
}

function patchSummary(client: QueryClient, id: string, patch: Partial<ConversationSummary>): void {
  mapList(client, (items) => items.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  client.setQueryData<ConversationDetail>(CONVERSATION_KEYS.detail(id), (detail) =>
    detail ? { ...detail, ...patch } : detail,
  );
}

/** Update the cached messages of a conversation. */
export function updateMessages(
  client: QueryClient,
  id: string,
  change: (messages: ConversationMessage[]) => ConversationMessage[],
  seed?: Omit<ConversationDetail, "messages">,
): void {
  client.setQueryData<ConversationDetail>(CONVERSATION_KEYS.detail(id), (detail) => {
    if (detail) return { ...detail, messages: change(detail.messages) };
    return seed ? { ...seed, messages: change([]) } : detail;
  });
}

// --- mutations -------------------------------------------------------------------------------

interface Snapshot {
  list: ListData | undefined;
  detail: ConversationDetail | undefined;
}

function snapshot(client: QueryClient, id: string): Snapshot {
  return {
    list: client.getQueryData<ListData>(CONVERSATION_KEYS.list),
    detail: client.getQueryData<ConversationDetail>(CONVERSATION_KEYS.detail(id)),
  };
}

function restore(client: QueryClient, id: string, saved: Snapshot | undefined): void {
  if (!saved) return;
  client.setQueryData(CONVERSATION_KEYS.list, saved.list);
  client.setQueryData(CONVERSATION_KEYS.detail(id), saved.detail);
}

export interface UpdateInput {
  conversation: ConversationSummary;
  patch: ConversationPatch;
}

/**
 * The newest version the page knows of a conversation. A chat started here is seeded from
 * `meta` without one; the list read after its first answer has it.
 */
function latestVersion(client: QueryClient, conversation: ConversationSummary): number {
  const listed = client
    .getQueryData<ListData>(CONVERSATION_KEYS.list)
    ?.pages.flatMap((page) => page.data)
    .find((item) => item.id === conversation.id);
  return Math.max(conversation.version, listed?.version ?? 0);
}

/** The fields a patch sets (null or absent: unchanged). */
function changesOf(patch: ConversationPatch): Partial<ConversationSummary> {
  return {
    ...(patch.title != null ? { title: patch.title } : {}),
    ...(patch.pinned != null ? { pinned: patch.pinned } : {}),
  };
}

/**
 * Rename or pin/unpin (PATCH with If-Match), shown at once, undone if the API refuses (422
 * `title_*`, 412 when it changed elsewhere: the chat is then read again for its new version).
 */
export function useUpdateConversation() {
  const api = useBffClient("staff");
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ conversation, patch }: UpdateInput): Promise<ConversationSummary> =>
      unwrap(
        api.PATCH("/api/v1/knowledge/conversations/{conversation_id}", {
          params: { path: { conversation_id: conversation.id } },
          headers: { "If-Match": ifMatch(latestVersion(client, conversation)) },
          body: patch,
        }),
      ),
    onMutate: async ({ conversation, patch }) => {
      await client.cancelQueries({ queryKey: CONVERSATION_KEYS.all });
      const saved = snapshot(client, conversation.id);
      patchSummary(client, conversation.id, changesOf(patch));
      return saved;
    },
    onError: (_error, { conversation }, saved) => {
      restore(client, conversation.id, saved);
      void client.invalidateQueries({ queryKey: CONVERSATION_KEYS.detail(conversation.id) });
    },
    onSuccess: (updated, { conversation }) => {
      patchSummary(client, conversation.id, updated);
    },
    onSettled: () => client.invalidateQueries({ queryKey: CONVERSATION_KEYS.list }),
  });
}

/** Delete (204), removed at once, put back if the API refuses. */
export function useDeleteConversation() {
  const api = useBffClient("staff");
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (conversation: ConversationSummary): Promise<void> => {
      await unwrap(
        api.DELETE("/api/v1/knowledge/conversations/{conversation_id}", {
          params: { path: { conversation_id: conversation.id } },
        }),
      );
    },
    onMutate: async (conversation) => {
      await client.cancelQueries({ queryKey: CONVERSATION_KEYS.all });
      const saved = snapshot(client, conversation.id);
      mapList(client, (items) => items.filter((item) => item.id !== conversation.id));
      return saved;
    },
    onError: (_error, conversation, saved) => restore(client, conversation.id, saved),
    onSuccess: (_result, conversation) => {
      client.removeQueries({ queryKey: CONVERSATION_KEYS.detail(conversation.id) });
    },
    onSettled: () => client.invalidateQueries({ queryKey: CONVERSATION_KEYS.list }),
  });
}

/**
 * A conversation's title for display, or null when it has none yet (the API sends "" then;
 * the screens say "New chat").
 */
export function titleOf(
  summary: Pick<ConversationSummary, "title"> | null | undefined,
): string | null {
  const title = summary?.title.trim();
  return title ? title : null;
}
