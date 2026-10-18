"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** A stable text form of a form's current values (repeated names keep their order). */
export function formSnapshot(form: HTMLFormElement): string {
  const pairs: [string, string][] = [];
  for (const [name, value] of new FormData(form).entries()) {
    if (typeof value === "string") pairs.push([name, value]);
  }
  return JSON.stringify(pairs);
}

/**
 * Dirty state for an explicit-save settings form (docs/17 §4.4, `SaveBar`): the form's values
 * are compared with the values it was first rendered with (the loaded settings). Put `ref` and
 * `onChange` on the `<form>`; controlled parts (switches, chosen lists) are re-checked after
 * every render of the owner. Discard by remounting the form with a new `key`.
 */
export function useFormDirty() {
  const ref = useRef<HTMLFormElement>(null);
  const initial = useRef<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const check = useCallback(() => {
    const form = ref.current;
    if (!form) return;
    const snapshot = formSnapshot(form);
    if (initial.current === null) initial.current = snapshot;
    setDirty(snapshot !== initial.current);
  }, []);

  useEffect(() => {
    check();
  });

  return { ref, dirty, onChange: check };
}
