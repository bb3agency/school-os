"""Tiny hand-built PDFs for the text-layer tests (ADR-0027). Synthetic content only.

No PDF-writing dependency: objects, a content stream per page and a correct ``xref`` table are
written here. English text uses the standard Helvetica font; Telugu (or "legacy font") text uses
a simple font whose ``ToUnicode`` CMap maps one-byte codes to the wanted code points, which is how
a real Telugu PDF with a proper Unicode font carries its text layer. An image-only page draws a
1x1 grey image (a scan); a page can also be blank. Encryption writes a Standard security handler
(RC4, revision 2) either with an empty user password (opens, but is encrypted) or with an unknown
one (needs a password); stream contents are not encrypted since the tests never get that far.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")
FILE_ID = bytes.fromhex("5ec3a1f00d5eed0000000000000000aa")

Encryption = Literal["none", "empty_user_password", "user_password"]


@dataclass(frozen=True, slots=True)
class Page:
    lines: Sequence[str] = ()
    """Text lines, top to bottom (empty for a blank or image-only page)."""
    image: bool = False
    """Draw a full-page image (what a scanner produces)."""
    unicode_font: bool = False
    """Encode the lines with a ToUnicode-mapped font (needed for Telugu)."""
    private_use: bool = False
    """Map the glyphs to Private Use code points (a legacy, non-Unicode Telugu font)."""
    extra_objects: int = 0
    """Additional empty path objects (to exercise the per-page object limit)."""


@dataclass(slots=True)
class _Writer:
    objects: list[bytes] = field(default_factory=list)

    def add(self, body: bytes) -> int:
        self.objects.append(body)
        return len(self.objects)

    def reserve(self) -> int:
        return self.add(b"")

    def set(self, ref: int, body: bytes) -> None:
        self.objects[ref - 1] = body

    def stream(self, data: bytes, extra: bytes = b"") -> int:
        return self.add(b"<< /Length %d %s>>\nstream\n%s\nendstream" % (len(data), extra, data))

    def build(self, root: int, encrypt: int | None) -> bytes:
        out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(self.objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(self.objects) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        trailer = b"/Size %d /Root %d 0 R /ID [<%s> <%s>]" % (
            len(self.objects) + 1,
            root,
            FILE_ID.hex().encode(),
            FILE_ID.hex().encode(),
        )
        if encrypt is not None:
            trailer += b" /Encrypt %d 0 R" % encrypt
        out += b"trailer\n<< %s >>\nstartxref\n%d\n%%%%EOF\n" % (trailer, xref)
        return bytes(out)


def _escape(data: bytes) -> bytes:
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _latin_line(line: str) -> bytes:
    return b"(" + _escape(line.encode("cp1252")) + b")"


def _mapped_font(w: _Writer, chars: Sequence[str], private_use: bool) -> tuple[int, dict[str, int]]:
    """A simple font whose codes 33.. map (ToUnicode) to ``chars``; space stays 32."""
    codes = {ch: 33 + i for i, ch in enumerate(chars)}
    if len(codes) > 200:  # pragma: no cover - test helper guard
        raise ValueError("too many distinct characters for one simple font")
    entries = [b"<20> <0020>"]
    for ch, code in codes.items():
        target = chr(0xE000 + code) if private_use else ch
        utf16 = target.encode("utf-16-be").hex().upper().encode()
        entries.append(b"<%02X> <%s>" % (code, utf16))
    cmap = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        b"/CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
        b"1 begincodespacerange <00> <FF> endcodespacerange\n"
        + b"%d beginbfchar\n" % len(entries)
        + b"\n".join(entries)
        + b"\nendbfchar\nendcmap CMapName currentdict /CMapResource defineresource pop end end"
    )
    to_unicode = w.stream(cmap)
    last = 33 + len(codes)
    widths = b" ".join(b"500" for _ in range(32, last + 1))
    font = w.add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /SyntheticTelugu /FirstChar 32 "
        b"/LastChar %d /Widths [%s] /ToUnicode %d 0 R >>" % (last, widths, to_unicode)
    )
    return font, {" ": 32, **codes}


def _content(page: Page, codes: dict[str, int] | None) -> bytes:
    ops: list[bytes] = []
    if page.image:
        ops.append(b"q 595 0 0 842 0 0 cm /Im1 Do Q")
    for _ in range(page.extra_objects):
        ops.append(b"0 0 m 1 1 l S")
    if page.lines:
        ops.append(b"BT /F1 12 Tf 16 TL 56 780 Td")
        for line in page.lines:
            if codes is None:
                ops.append(_latin_line(line) + b" Tj T*")
            else:
                ops.append(b"<" + bytes(codes[ch] for ch in line).hex().encode() + b"> Tj T*")
        ops.append(b"ET")
    return b"\n".join(ops)


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def _encrypt_dict(mode: Encryption) -> bytes:
    owner = hashlib.md5(b"synthetic owner").digest() * 2  # noqa: S324 - PDF RC4 R2 test vector
    permissions = -3904
    if mode == "empty_user_password":
        key = hashlib.md5(  # noqa: S324 - PDF standard security handler, revision 2
            _PAD + owner + struct.pack("<i", permissions) + FILE_ID
        ).digest()[:5]
        user = _rc4(key, _PAD)
    else:
        user = hashlib.md5(b"not the empty password").digest() * 2  # noqa: S324 - test bytes
    return b"<< /Filter /Standard /V 1 /R 2 /Length 40 /O <%s> /U <%s> /P %d >>" % (
        owner.hex().encode(),
        user.hex().encode(),
        permissions,
    )


def pdf(pages: Sequence[Page], *, encryption: Encryption = "none") -> bytes:
    """A PDF with one page per :class:`Page` (A4, text top-left)."""
    w = _Writer()
    catalog = w.reserve()
    tree = w.reserve()
    helvetica = w.add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    image = w.stream(
        b"\x80",
        b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 ",
    )
    kids = []
    for page in pages:
        codes = None
        font = helvetica
        if page.unicode_font:
            chars = sorted({ch for line in page.lines for ch in line if ch != " "})
            font, codes = _mapped_font(w, chars, page.private_use)
        content = w.stream(_content(page, codes))
        kids.append(
            w.add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
                b"/Resources << /Font << /F1 %d 0 R >> /XObject << /Im1 %d 0 R >> >> >>"
                % (tree, content, font, image)
            )
        )
    w.set(catalog, b"<< /Type /Catalog /Pages %d 0 R >>" % tree)
    refs = b" ".join(b"%d 0 R" % k for k in kids)
    w.set(tree, b"<< /Type /Pages /Kids [%s] /Count %d >>" % (refs, len(kids)))
    encrypt = None if encryption == "none" else w.add(_encrypt_dict(encryption))
    return w.build(catalog, encrypt)


def text_page(*lines: str) -> Page:
    return Page(lines=lines)


def telugu_page(*lines: str) -> Page:
    return Page(lines=lines, unicode_font=True)


__all__ = ["FILE_ID", "Encryption", "Page", "pdf", "telugu_page", "text_page"]
