/**
 * Heading levels that skip one (h1 → h3) in document order (WCAG 1.3.1, 2.4.6; axe
 * `heading-order`). Screen-reader users move by heading level: a skipped level reads as a
 * missing section. Returns "h1 → h3 “Sources”" lines, empty when the outline is sound.
 */
export function headingSkips(root: ParentNode = document): string[] {
  const skips: string[] = [];
  let previous = 0;
  for (const heading of root.querySelectorAll<HTMLElement>("h1, h2, h3, h4, h5, h6")) {
    const level = Number(heading.tagName.slice(1));
    if (previous > 0 && level > previous + 1) {
      skips.push(`h${previous} → h${level} “${heading.textContent?.trim() ?? ""}”`);
    }
    previous = level;
  }
  return skips;
}
