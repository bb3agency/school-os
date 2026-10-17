import { TELUGU_STYLESHEET, teluguStylesheet } from "@/i18n/telugu-font";
import { readTeluguFont } from "@/i18n/telugu-font-files";
import { PUBLIC_TELUGU_FONT_BASE } from "@/server/public/messages";

/**
 * GET /p/fonts/telugu/{file}: the self-hosted Telugu font for parent pages (ADR-0044 decision 3,
 * FR-PUB-008). Parent pages are Telugu when the school or guardian chose Telugu, whatever
 * SOS_TELUGU_ENABLED says (that switch is for staff screens), so this route does not check it.
 * Only Telugu parent pages link it; staff pages never do. Fonts carry no data.
 */
export const dynamic = "force-dynamic";

const notFound = () =>
  new Response(null, { status: 404, headers: { "Cache-Control": "no-store" } });

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ file: string }> },
): Promise<Response> {
  const { file } = await params;
  if (file === TELUGU_STYLESHEET) {
    return new Response(teluguStylesheet(PUBLIC_TELUGU_FONT_BASE), {
      headers: {
        "Content-Type": "text/css; charset=utf-8",
        "Cache-Control": "public, max-age=86400",
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
