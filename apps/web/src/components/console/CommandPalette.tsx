"use client";

/*
 * Command palette (docs/17 §5.9).
 *
 * Adapted from interior.dev (github.com/ddoemonn/interior @3148000), MIT License,
 * Copyright (c) 2026 ozzy; via the Calevate reference copy. Full notice: ./LICENSE-interior.
 * Changes: the layer is the native modal <dialog> (focus trapped, page behind inert, Escape
 * closes, focus returns to the trigger) instead of a portal and a focus-trap hook; strings
 * come from next-intl; items navigate through the locale-free router or run an action;
 * SchoolOS tokens; 44px rows on touch; the open motion is CSS only (dialog.palette) and off
 * under reduced motion.
 *
 * Server search (D72, docs/17 §5.9): a console may pass `search` (the school console passes
 * student search over the scoped POST /students/search). It runs 250 ms after typing stops,
 * from 2 characters, keeps at most 8 rows, and aborts the previous request on new input. Its
 * rows are never remembered: "Recent" holds the ids of static pages and actions only, in
 * memory for the tab (no browser storage), so no name or student id outlives the palette.
 */

import { useTranslations } from "next-intl";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Icon, type IconName } from "@/components/ui/Icon";
import { Kbd } from "@/components/ui/Kbd";
import { useRouter } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { rankItems } from "./rank";

export interface CommandItem {
  id: string;
  label: string;
  /** The group heading it is listed under (one of `groups`). */
  group: string;
  /** Navigate here (a locale-free path of an existing page)… */
  href?: string;
  /** …or run this (density switch, open a dialog). */
  onRun?: () => void;
  /** A short fact on the right of the row ("Records", "Fees"). */
  hint?: string;
  /** Extra words the query may match (synonyms, the section name). */
  keywords?: string;
  icon?: IconName;
  /** A keyboard shortcut shown on the row ("Ctrl \\"). */
  shortcut?: string;
}

/** A server search shown as its own group after the static groups (D72). */
export interface PaletteSearch {
  /** Group heading ("Students"). */
  group: string;
  /** Rows for `query` (trimmed, at least SEARCH_MIN_CHARS long); must honour `signal`. */
  run: (query: string, signal: AbortSignal) => Promise<readonly CommandItem[]>;
  /** Polite status lines for the search (shown and announced). */
  messages: { loading: string; empty: string; error: string; offline: string };
}

/** Typing pause before a server search (ms). */
export const SEARCH_DEBOUNCE_MS = 250;
/** Shortest query sent to the server. */
export const SEARCH_MIN_CHARS = 2;
/** Most server rows shown. */
export const SEARCH_MAX_RESULTS = 8;
/** Most recent pages and actions kept. */
const RECENT_MAX = 5;
const RECENT_PREFIX = "recent:";

/*
 * Recently run pages and actions, newest first: ids of the static items only (never a server
 * search row, so never a name or a student id). Module memory: it lasts for the tab and is
 * gone on reload; nothing is written to localStorage or sessionStorage (shared office PCs).
 */
let recentIds: readonly string[] = [];

function remember(id: string) {
  recentIds = [id, ...recentIds.filter((other) => other !== id)].slice(0, RECENT_MAX);
}

/** The item ids "Recent" holds now, newest first. */
export function paletteRecents(): readonly string[] {
  return recentIds;
}

/** Forget the recents (tests; a console may call it on sign-out). */
export function clearPaletteRecents() {
  recentIds = [];
}

type RemoteState =
  | { status: "idle" }
  | { status: "loading"; query: string }
  | { status: "ready"; query: string; items: readonly CommandItem[] }
  | { status: "error" | "offline"; query: string };

const NO_ITEMS: readonly CommandItem[] = [];

function isOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  items: readonly CommandItem[];
  /** Group headings in display order; a group with no match is left out. */
  groups: readonly string[];
  /** Optional server search (students), listed after `groups`. */
  search?: PaletteSearch | undefined;
  /** Placeholder of the box while `search` is on. */
  searchPlaceholder?: string | undefined;
}

/**
 * Ctrl/⌘+K palette: one search box (WAI-ARIA combobox with `aria-activedescendant`) over a
 * grouped listbox. Up/Down move, Enter runs, Escape closes. Focus stays in the box, so the
 * options are not focusable. Results are announced politely after typing stops.
 */
export function CommandPalette({
  open,
  onClose,
  items,
  groups,
  search,
  searchPlaceholder,
}: CommandPaletteProps) {
  const t = useTranslations("ui.palette");
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  // A click on the backdrop lands on the <dialog> box itself.
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const onClick = (event: MouseEvent) => {
      if (event.target === dialog) closeRef.current();
    };
    dialog.addEventListener("click", onClick);
    return () => dialog.removeEventListener("click", onClick);
  }, []);

  return (
    <dialog
      ref={dialogRef}
      aria-label={t("label")}
      className="palette"
      data-print="hide"
      onClose={() => {
        if (open) onClose();
      }}
    >
      {/* The body (and the router it needs) exists only while the palette is open. */}
      {open ? (
        <PaletteBody
          items={items}
          groups={groups}
          search={search}
          placeholder={search ? searchPlaceholder : undefined}
          onDone={onClose}
        />
      ) : null}
    </dialog>
  );
}

function PaletteBody({
  items,
  groups,
  search,
  placeholder,
  onDone,
}: {
  items: readonly CommandItem[];
  groups: readonly string[];
  search: PaletteSearch | undefined;
  placeholder: string | undefined;
  onDone: () => void;
}) {
  const t = useTranslations("ui.palette");
  const router = useRouter();
  const uid = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const pointer = useRef({ x: -1, y: -1 });
  const [query, setQuery] = useState("");
  const [pinned, setPinned] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [remote, setRemote] = useState<RemoteState>({ status: "idle" });
  // The recents as they were when the palette opened (stable while it is open).
  const [recents] = useState(() => recentIds);
  const text = query.trim();
  const searching = search !== undefined && text.length >= SEARCH_MIN_CHARS;

  // Server search: debounced, from 2 characters, the previous request aborted on new input.
  const runSearch = search?.run;
  useEffect(() => {
    if (!runSearch || text.length < SEARCH_MIN_CHARS) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      if (isOffline()) {
        setRemote({ status: "offline", query: text });
        return;
      }
      setRemote({ status: "loading", query: text });
      runSearch(text, controller.signal)
        .then((rows) => {
          if (controller.signal.aborted) return;
          setRemote({ status: "ready", query: text, items: rows.slice(0, SEARCH_MAX_RESULTS) });
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          setRemote({ status: isOffline() ? "offline" : "error", query: text });
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [runSearch, text]);

  // The search as it stands for the text in the box: rows only for this exact query; while
  // the pause or the request is pending, "loading".
  const remoteNow: RemoteState = !searching
    ? { status: "idle" }
    : remote.status !== "idle" && remote.status !== "loading" && remote.query === text
      ? remote
      : { status: "loading", query: text };
  const remoteItems = remoteNow.status === "ready" ? remoteNow.items : NO_ITEMS;

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const recentGroup = t("groupRecent");
  const searchGroup = search?.group;
  const sections = useMemo(() => {
    const ranked = rankItems(items, query);
    const fixed = groups
      .map((group) => ({ group, items: ranked.filter((item) => item.group === group) }))
      .filter((section) => section.items.length > 0);
    // "Recent" (empty box only): the static items still offered now, so a permission that
    // was taken away also removes its recent entry.
    const recent = query.trim()
      ? []
      : recents
          .map((id) => items.find((item) => item.id === id))
          .filter((item): item is CommandItem => item !== undefined)
          .map((item) => ({ ...item, id: `${RECENT_PREFIX}${item.id}`, group: recentGroup }));
    const head = recent.length > 0 ? [{ group: recentGroup, items: recent }] : [];
    // The search rows come after the static groups, so the row under Enter does not jump
    // when they arrive.
    const tail =
      searchGroup !== undefined && remoteItems.length > 0
        ? [
            {
              group: searchGroup,
              items: remoteItems.map((item) => ({ ...item, group: searchGroup })),
            },
          ]
        : [];
    return [...head, ...fixed, ...tail];
  }, [items, groups, query, recents, recentGroup, searchGroup, remoteItems]);
  const flat = useMemo(() => sections.flatMap((section) => section.items), [sections]);
  const activeId = flat.some((item) => item.id === pinned) ? pinned : (flat[0]?.id ?? null);
  const activeIndex = flat.findIndex((item) => item.id === activeId);
  const optionId = (id: string) => `${uid}-opt-${id}`;

  // The search's own status line: shown under the results and added to the announcement.
  const remoteLine =
    search === undefined || remoteNow.status === "idle"
      ? ""
      : remoteNow.status === "ready"
        ? remoteNow.items.length === 0
          ? search.messages.empty
          : ""
        : search.messages[remoteNow.status];

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const count = flat.length === 0 ? t("empty") : t("results", { count: flat.length });
      setAnnouncement(remoteLine ? `${count} ${remoteLine}` : count);
    }, 400);
    return () => window.clearTimeout(timer);
  }, [flat.length, remoteLine, t]);

  const close = () => onDone();

  const run = (item: CommandItem) => {
    // Only static pages and actions are remembered; a search row (a student) never is.
    const original = item.id.startsWith(RECENT_PREFIX)
      ? item.id.slice(RECENT_PREFIX.length)
      : item.id;
    if (items.some((known) => known.id === original)) remember(original);
    close();
    if (item.href) router.push(item.href);
    else item.onRun?.();
  };

  const jump = (index: number) => {
    if (flat.length === 0) return;
    const next = flat[(index + flat.length) % flat.length];
    if (!next) return;
    setPinned(next.id);
    document.getElementById(optionId(next.id))?.scrollIntoView?.({ block: "nearest" });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      jump(activeIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      jump((activeIndex < 0 ? 0 : activeIndex) - 1);
    } else if (event.key === "Home" && event.ctrlKey) {
      event.preventDefault();
      jump(0);
    } else if (event.key === "End" && event.ctrlKey) {
      event.preventDefault();
      jump(flat.length - 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const target = flat[activeIndex];
      if (target) run(target);
    }
  };

  return (
    <div className="flex max-h-[inherit] flex-col">
      <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-border px-4 pointer-coarse:h-14">
        <Icon name="search" className="size-4 shrink-0 text-ink-subtle" />
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-label={t("label")}
          aria-expanded="true"
          aria-controls={`${uid}-list`}
          aria-autocomplete="list"
          aria-activedescendant={activeId ? optionId(activeId) : undefined}
          autoComplete="off"
          spellCheck={false}
          enterKeyHint="go"
          value={query}
          placeholder={placeholder ?? t("placeholder")}
          onChange={(event) => {
            setQuery(event.target.value);
            setPinned(null);
            if (listRef.current) listRef.current.scrollTop = 0;
          }}
          onKeyDown={onKeyDown}
          className="h-full min-w-0 flex-1 bg-transparent text-base text-ink outline-none placeholder:text-ink-subtle"
        />
        <Kbd className="max-sm:hidden">Esc</Kbd>
      </div>
      {/* The combobox owns the keyboard (aria-activedescendant): options are not
              focusable, and a mouse down keeps focus in the box. */}
      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions -- APG combobox: a mouse down on the list keeps focus in the input (aria-activedescendant) */}
      <div
        ref={listRef}
        onMouseDown={(event) => event.preventDefault()}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1.5"
      >
        <div id={`${uid}-list`} role="listbox" aria-label={t("results", { count: flat.length })}>
          {sections.map((section, index) => (
            <div
              key={section.group}
              role="group"
              aria-labelledby={`${uid}-g-${index}`}
              className="py-1"
            >
              <div
                id={`${uid}-g-${index}`}
                role="presentation"
                className="px-2.5 pt-1.5 pb-1 text-xs font-semibold text-ink-subtle"
              >
                {section.group}
              </div>
              {section.items.map((item) => {
                const active = item.id === activeId;
                return (
                  // Keyboard selection is the combobox's (Up, Down, Enter), see the listbox.
                  // eslint-disable-next-line jsx-a11y/interactive-supports-focus, jsx-a11y/click-events-have-key-events
                  <div
                    key={item.id}
                    id={optionId(item.id)}
                    role="option"
                    aria-selected={active}
                    onPointerMove={(event) => {
                      const { x, y } = pointer.current;
                      if (event.clientX === x && event.clientY === y) return;
                      pointer.current = { x: event.clientX, y: event.clientY };
                      if (!active) setPinned(item.id);
                    }}
                    onClick={() => run(item)}
                    className={cn(
                      "flex min-h-9 cursor-default items-center gap-3 rounded-md px-2.5 py-1.5 text-sm pointer-coarse:min-h-11",
                      active ? "bg-accent-soft text-accent-strong" : "text-ink",
                    )}
                  >
                    {item.icon ? (
                      <Icon
                        name={item.icon}
                        className={cn("size-4 shrink-0", active ? "" : "text-ink-subtle")}
                      />
                    ) : null}
                    <span className="min-w-0 flex-1 break-anywhere">{item.label}</span>
                    {item.hint ? (
                      <span className="max-w-[45%] shrink-0 text-xs text-ink-subtle">
                        {item.hint}
                      </span>
                    ) : null}
                    {item.shortcut ? <Kbd>{item.shortcut}</Kbd> : null}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        {flat.length > 0 && remoteLine ? (
          <p className="px-2.5 py-2 text-sm text-ink-muted">{remoteLine}</p>
        ) : null}
        {flat.length === 0 ? (
          <p className="px-3 py-8 text-center text-sm text-ink-muted">
            {remoteNow.status === "loading" ? remoteLine : t("empty")}
            {remoteNow.status === "error" || remoteNow.status === "offline" ? (
              <span className="mt-1 block">{remoteLine}</span>
            ) : null}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-t border-border bg-surface-muted px-4 py-2 text-xs text-ink-muted max-sm:hidden">
        <span className="inline-flex items-center gap-1.5">
          <Kbd>↑</Kbd>
          <Kbd>↓</Kbd>
          {t("hintMove")}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Kbd>Enter</Kbd>
          {t("hintOpen")}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Kbd>Esc</Kbd>
          {t("hintClose")}
        </span>
      </div>
      <span role="status" aria-live="polite" className="sr-only">
        {announcement}
      </span>
    </div>
  );
}
