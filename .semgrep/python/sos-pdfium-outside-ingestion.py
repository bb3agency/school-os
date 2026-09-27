# ruff: noqa
# mypy: ignore-errors
# Test fixture for `semgrep --test .semgrep`. Never imported or executed.
import importlib

# ruleid: sos-pdfium-outside-ingestion
import pypdfium2

# ruleid: sos-pdfium-outside-ingestion
import pypdfium2 as pdfium

# ruleid: sos-pdfium-outside-ingestion
import pypdfium2.raw as pdfium_c

# ruleid: sos-pdfium-outside-ingestion
from pypdfium2 import PdfDocument

# ruleid: sos-pdfium-outside-ingestion
from pypdfium2_raw import bindings


def lazy() -> object:
    # ruleid: sos-pdfium-outside-ingestion
    import pypdfium2

    return pypdfium2


# ruleid: sos-pdfium-outside-ingestion
lib = importlib.import_module("pypdfium2")

# ok: sos-pdfium-outside-ingestion
from app.knowledge.ingestion.extract import extract_pages

# ok: sos-pdfium-outside-ingestion
import pypdfium2_style_notes

# ok: sos-pdfium-outside-ingestion
library = "pypdfium2"

__all__ = ["PdfDocument", "bindings", "extract_pages", "lazy", "lib", "pdfium", "pdfium_c"]
