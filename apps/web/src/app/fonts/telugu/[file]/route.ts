import { teluguEnabled } from "@/i18n/languages";
import { readTeluguFont, TELUGU_STYLESHEET, teluguStylesheet } from "@/i18n/telugu-font";

/**
 * GET /fonts/telugu/{file}: the Telugu font stylesheet and files (ADR-0036). Public (fonts
 * carry no data), and only while SOS_TELUGU_ENABLED is on: otherwise 404, so no Telugu font
 * can be loaded at all. Read per request, so switching Telugu on or off needs no rebuild.
 */
export const dynamic = "force-dynamic";

const notFound = () =>
  new Response(null, { status: 404, headers: { "Cache-Control": "no-store" } });

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ file: string }> },
): Promise<Response> {
  if (!teluguEnabled()) return notFound();
  const { file } = await params;
  if (file === TELUGU_STYLESHEET) {
    return new Response(teluguStylesheet(), {
      headers: {
        "Content-Type": "text/css; charset=utf-8",
        // Short: the switch may change without a new build.
        "Cache-Control": "public, max-age=300",
        "X-Content-Type-Options": "nosniff",
      },
    });
  }
  const font = await readTeluguFont(file);
  if (!font) return notFound();
  return new Response(font, {
    headers: {
      "Content-Type": "font/woff2",
      "Cache-Control": "public, max-age=86400",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
