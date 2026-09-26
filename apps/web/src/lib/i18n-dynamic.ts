/**
 * Translators with keys only known at run time (API codes, zod message keys). next-intl's
 * typed `t` accepts literal keys only; this narrows the escape hatch to one place and always
 * falls back to a key that exists.
 */
type LooseTranslator = ((key: string, values?: Record<string, string | number>) => string) & {
  has: (key: string) => boolean;
};

export function translateOr(
  t: unknown,
  key: string,
  fallback: string,
  values?: Record<string, string | number>,
): string {
  const loose = t as LooseTranslator;
  return loose.has(key) ? loose(key, values) : loose(fallback, values);
}
