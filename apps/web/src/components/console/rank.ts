/*
 * Fuzzy ranking for the command palette.
 *
 * Adapted from interior.dev (github.com/ddoemonn/interior @3148000), MIT License,
 * Copyright (c) 2026 ozzy; via the Calevate reference copy in
 * docs/design/reference/calevate/components/interior/command-palette.tsx. Full notice:
 * ./LICENSE-interior. Changes: typed for SchoolOS (strict, no `any`), NFC-normalised and
 * case-folded text so Telugu and accented labels match as typed.
 */

const BOUNDARY = /[\s\-_/.:]/;

export interface RankItem {
  id: string;
  label: string;
  /** Extra text the query may match (a synonym, the group, a path). */
  keywords?: string;
}

function fold(text: string): string {
  return text.normalize("NFC").toLocaleLowerCase();
}

/** Score of `query` (already folded) as a subsequence of `text`; -1 when it does not match. */
export function scoreOne(text: string, query: string): number {
  const folded = fold(text);
  let cursor = 0;
  let total = 0;
  let streak = 0;
  for (let i = 0; i < query.length; i += 1) {
    const at = folded.indexOf(query.charAt(i), cursor);
    if (at < 0) return -1;
    streak = at === cursor && i > 0 ? streak + 1 : 0;
    total += 2 + streak * 4;
    if (at === 0) total += 12;
    else if (BOUNDARY.test(folded.charAt(at - 1))) total += 8;
    cursor = at + 1;
  }
  return total;
}

/**
 * Items whose label (or, slightly lower, keywords) contains the query's letters in order,
 * best first; the list order breaks ties. An empty query returns every item in order.
 */
export function rankItems<T extends RankItem>(items: readonly T[], query: string): T[] {
  const q = fold(query.trim());
  if (!q) return [...items];
  const scored: Array<{ item: T; score: number; order: number }> = [];
  items.forEach((item, order) => {
    const direct = scoreOne(item.label, q);
    const aliased = item.keywords ? scoreOne(item.keywords, q) - 3 : -1;
    const best = Math.max(direct, aliased);
    if (best < 0) return;
    scored.push({ item, score: best - item.label.length * 0.05, order });
  });
  scored.sort((a, b) => b.score - a.score || a.order - b.order);
  return scored.map((entry) => entry.item);
}
