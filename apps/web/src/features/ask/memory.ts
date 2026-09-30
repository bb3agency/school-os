"use client";

import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError } from "@/lib/bff/query";
import { useAskContractApi, type MemoryItem, type MemorySettings } from "./contract";

/**
 * Ask memory (the signed-in member's own preferences and work context, never facts about
 * students or staff): the list, the on/off setting, and add / edit / delete / confirm /
 * forget everything with optimistic updates that roll back when the API refuses.
 */

export const MEMORY_KEYS = {
  all: ["staff", "knowledge", "memory"] as const,
  items: ["staff", "knowledge", "memory", "items"] as const,
  settings: ["staff", "knowledge", "memory", "settings"] as const,
};

/** Longest memory the page offers to save (the API checks again). */
export const MEMORY_MAX = 500;

const retry = (count: number, error: unknown) =>
  count < 1 &&
  !(error instanceof NotAvailableError) &&
  !(error instanceof AuthRedirectError) &&
  !(error instanceof ApiError && error.status < 500);

/** GET /knowledge/memory-settings (`school_enabled` is read-only). */
export function useMemorySettings(enabled = true) {
  const api = useAskContractApi();
  return useQuery({
    queryKey: MEMORY_KEYS.settings,
    queryFn: () => api.getMemorySettings(),
    enabled,
    retry,
    staleTime: 60_000,
  });
}

/** Memory is in use for Ask: the school allows it and the member has it on. */
export function memoryOn(settings: MemorySettings | undefined): boolean {
  return Boolean(settings?.school_enabled && settings.enabled);
}

/** GET /knowledge/memories. */
export function useMemories(enabled = true) {
  const api = useAskContractApi();
  return useQuery({
    queryKey: MEMORY_KEYS.items,
    queryFn: () => api.listMemories(),
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

function rollback(client: QueryClient, saved: MemoryItem[] | undefined): void {
  client.setQueryData(MEMORY_KEYS.items, saved);
}

function settle(client: QueryClient) {
  return client.invalidateQueries({ queryKey: MEMORY_KEYS.items });
}

/** PUT /knowledge/memory-settings, switched at once, undone if refused. */
export function useSetMemoryEnabled() {
  const api = useAskContractApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => api.putMemorySettings(enabled),
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

/** POST /knowledge/memories: shown at once as a temporary row. */
export function useAddMemory() {
  const api = useAskContractApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => api.addMemory(text),
    onMutate: async (text) => {
      const saved = await begin(client);
      const now = new Date().toISOString();
      setItems(client, (items) => [
        {
          id: `pending-${now}`,
          text,
          source: "explicit",
          status: "saved",
          created_at: now,
          updated_at: now,
          version: 0,
        },
        ...items,
      ]);
      return saved;
    },
    onError: (_error, _text, saved) => rollback(client, saved),
    onSettled: () => settle(client),
  });
}

/** PATCH /knowledge/memories/{id} (If-Match). */
export function useEditMemory() {
  const api = useAskContractApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ item, text }: { item: MemoryItem; text: string }) => api.editMemory(item, text),
    onMutate: async ({ item, text }) => {
      const saved = await begin(client);
      setItems(client, (items) => items.map((m) => (m.id === item.id ? { ...m, text } : m)));
      return saved;
    },
    onError: (_error, _input, saved) => rollback(client, saved),
    onSuccess: (updated) => {
      if (updated)
        setItems(client, (items) => items.map((m) => (m.id === updated.id ? updated : m)));
    },
    onSettled: () => settle(client),
  });
}

/** DELETE /knowledge/memories/{id} (also "Dismiss" on a suggestion). */
export function useDeleteMemory() {
  const api = useAskContractApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteMemory(id),
    onMutate: async (id) => {
      const saved = await begin(client);
      setItems(client, (items) => items.filter((m) => m.id !== id));
      return saved;
    },
    onError: (_error, _id, saved) => rollback(client, saved),
    onSettled: () => settle(client),
  });
}

/** POST /knowledge/memories/{id}/confirm ("Save" on a suggestion). */
export function useConfirmMemory() {
  const api = useAskContractApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.confirmMemory(id),
    onMutate: async (id) => {
      const saved = await begin(client);
      setItems(client, (items) =>
        items.map((m) => (m.id === id ? { ...m, status: "saved" as const } : m)),
      );
      return saved;
    },
    onError: (_error, _id, saved) => rollback(client, saved),
    onSettled: () => settle(client),
  });
}

/** DELETE /knowledge/memories: forget everything. */
export function useForgetAllMemories() {
  const api = useAskContractApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.forgetAllMemories(),
    onMutate: async () => {
      const saved = await begin(client);
      client.setQueryData<MemoryItem[]>(MEMORY_KEYS.items, []);
      return saved;
    },
    onError: (_error, _input, saved) => rollback(client, saved),
    onSettled: () => settle(client),
  });
}
