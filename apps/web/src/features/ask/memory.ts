"use client";

import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, asList, NotAvailableError, unwrap, useBffClient } from "@/lib/bff/query";
import { ifMatch, type MemoryItem, type MemorySettings } from "./data";

/**
 * Ask memory (ADR-0034: the signed-in member's own preferences and work context, never facts
 * about students or staff): the list, the on/off setting, and add / edit / delete / confirm /
 * forget everything with optimistic updates that roll back when the API refuses.
 */

export const MEMORY_KEYS = {
  all: ["staff", "knowledge", "memory"] as const,
  items: ["staff", "knowledge", "memory", "items"] as const,
  settings: ["staff", "knowledge", "memory", "settings"] as const,
};

/** Longest memory the API stores (MemoryIn.text: 1-200 characters; it checks again). */
export const MEMORY_MAX = 200;
/** Most items one member may keep in a school, pending suggestions included (409 memory_full). */
export const MEMORY_LIMIT = 30;

const retry = (count: number, error: unknown) =>
  count < 1 &&
  !(error instanceof NotAvailableError) &&
  !(error instanceof AuthRedirectError) &&
  !(error instanceof ApiError && error.status < 500);

/** GET /knowledge/memory-settings (`school_enabled` is read-only here). */
export function useMemorySettings(enabled = true) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: MEMORY_KEYS.settings,
    queryFn: (): Promise<MemorySettings> => unwrap(api.GET("/api/v1/knowledge/memory-settings")),
    enabled,
    retry,
    staleTime: 60_000,
  });
}

/** Memory is in use for Ask: the school allows it and the member has it on. */
export function memoryOn(settings: MemorySettings | undefined): boolean {
  return Boolean(settings?.school_enabled && settings.enabled);
}

/** GET /knowledge/memories: one page with every item (confirmed and pending), newest first. */
export function useMemories(enabled = true) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: MEMORY_KEYS.items,
    queryFn: async (): Promise<MemoryItem[]> =>
      asList((await unwrap(api.GET("/api/v1/knowledge/memories"))).data),
    enabled,
    retry,
  });
}

function setItems(client: QueryClient, change: (items: MemoryItem[]) => MemoryItem[]): void {
  client.setQueryData<MemoryItem[]>(MEMORY_KEYS.items, (items) => change(items ?? []));
}

async function begin(client: QueryClient): Promise<MemoryItem[] | undefined> {
  await client.cancelQueries({ queryKey: MEMORY_KEYS.items });
  return client.getQueryData<MemoryItem[]>(MEMORY_KEYS.items);
}

/**
 * Undo an optimistic change. When the API said memory is off (409 memory_off: the school may
 * have switched it off meanwhile) the switch is read again so the page shows why.
 */
function rollback(client: QueryClient, saved: MemoryItem[] | undefined, error: unknown): void {
  client.setQueryData(MEMORY_KEYS.items, saved);
  if (error instanceof ApiError && error.code === "memory_off") {
    void client.invalidateQueries({ queryKey: MEMORY_KEYS.settings });
  }
}

function settle(client: QueryClient) {
  return client.invalidateQueries({ queryKey: MEMORY_KEYS.items });
}

/** PUT /knowledge/memory-settings, switched at once, undone if refused. */
export function useSetMemoryEnabled() {
  const api = useBffClient("staff");
  const client = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean): Promise<MemorySettings> =>
      unwrap(api.PUT("/api/v1/knowledge/memory-settings", { body: { enabled } })),
    onMutate: async (enabled) => {
      await client.cancelQueries({ queryKey: MEMORY_KEYS.settings });
      const saved = client.getQueryData<MemorySettings>(MEMORY_KEYS.settings);
      if (saved) client.setQueryData<MemorySettings>(MEMORY_KEYS.settings, { ...saved, enabled });
      return saved;
    },
    onError: (_error, _enabled, saved) => client.setQueryData(MEMORY_KEYS.settings, saved),
    onSuccess: (settings) => client.setQueryData(MEMORY_KEYS.settings, settings),
  });
}

/** POST /knowledge/memories (201): shown at once as a temporary row. */
export function useAddMemory() {
  const api = useBffClient("staff");
  const client = useQueryClient();
  return useMutation({
    mutationFn: (text: string): Promise<MemoryItem> =>
      unwrap(api.POST("/api/v1/knowledge/memories", { body: { text } })),
    onMutate: async (text) => {
      const saved = await begin(client);
      const now = new Date().toISOString();
      setItems(client, (items) => [
        {
          id: `pending-${now}`,
          text,
          source: "explicit",
          status: "active",
          created_at: now,
          updated_at: now,
          expires_at: null,
          version: 0,
        },
        ...items,
      ]);
      return saved;
    },
    onError: (error, _text, saved) => rollback(client, saved, error),
    onSettled: () => settle(client),
  });
}

/** PATCH /knowledge/memories/{id} (If-Match; the text is checked again like a new item). */
export function useEditMemory() {
  const api = useBffClient("staff");
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ item, text }: { item: MemoryItem; text: string }): Promise<MemoryItem> =>
      unwrap(
        api.PATCH("/api/v1/knowledge/memories/{memory_id}", {
          params: { path: { memory_id: item.id } },
          headers: { "If-Match": ifMatch(item.version) },
          body: { text },
        }),
      ),
    onMutate: async ({ item, text }) => {
      const saved = await begin(client);
      setItems(client, (items) => items.map((m) => (m.id === item.id ? { ...m, text } : m)));
      return saved;
    },
    onError: (error, _input, saved) => rollback(client, saved, error),
    onSuccess: (updated) => {
      setItems(client, (items) => items.map((m) => (m.id === updated.id ? updated : m)));
    },
    onSettled: () => settle(client),
  });
}

/** DELETE /knowledge/memories/{id} (also "Dismiss" on a suggestion). */
export function useDeleteMemory() {
  const api = useBffClient("staff");
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await unwrap(
        api.DELETE("/api/v1/knowledge/memories/{memory_id}", {
          params: { path: { memory_id: id } },
        }),
      );
    },
    onMutate: async (id) => {
      const saved = await begin(client);
      setItems(client, (items) => items.filter((m) => m.id !== id));
      return saved;
    },
    onError: (error, _id, saved) => rollback(client, saved, error),
    onSettled: () => settle(client),
  });
}

/**
 * POST /knowledge/memories/{id}/confirm ("Save" on a suggestion: pending → active). A
 * suggestion not confirmed within 24 hours is gone (404).
 */
export function useConfirmMemory() {
  const api = useBffClient("staff");
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string): Promise<MemoryItem> =>
      unwrap(
        api.POST("/api/v1/knowledge/memories/{memory_id}/confirm", {
          params: { path: { memory_id: id } },
        }),
      ),
    onMutate: async (id) => {
      const saved = await begin(client);
      setItems(client, (items) =>
        items.map((m) => (m.id === id ? { ...m, status: "active" as const, expires_at: null } : m)),
      );
      return saved;
    },
    onError: (error, _id, saved) => rollback(client, saved, error),
    onSettled: () => settle(client),
  });
}

/** DELETE /knowledge/memories: forget everything (204). */
export function useForgetAllMemories() {
  const api = useBffClient("staff");
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<void> => {
      await unwrap(api.DELETE("/api/v1/knowledge/memories"));
    },
    onMutate: async () => {
      const saved = await begin(client);
      client.setQueryData<MemoryItem[]>(MEMORY_KEYS.items, []);
      return saved;
    },
    onError: (error, _input, saved) => rollback(client, saved, error),
    onSettled: () => settle(client),
  });
}
