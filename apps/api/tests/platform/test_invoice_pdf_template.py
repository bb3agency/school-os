"""Invoice PDF template v0 (docs/16 §5.8, §19 Q2-Q3; FR-PLT-016, FR-PLT-017; CGST Rule 46).

Pure tests (no database, no Chromium): the document built from an issued invoice's snapshot,
the escaped A4 HTML, money formatting and words, and the storage key rules. The real render
with text extraction runs at the end and is skipped only when Chromium cannot be launched.
All values are synthetic.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.core.languages import contains_telugu
from app.core.pdf import FONT_URL, ChromiumRenderer
from app.platform import invoice_pdf as ip
from app.platform.invoice_storage import MemoryInvoiceStore, object_key

D = Decimal
SUPPLIER_ADDRESS = "Door 1-2, Synthetic Road; Vijayawada 520001, Andhra Pradesh"


def _invoice(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "status": "issued",
        "invoice_number": "SOS/26-27/000123",
        "financial_year": "2026-27",
        "issue_date": dt.date(2026, 10, 1),
        "due_date": dt.date(2026, 10, 16),
        "period_start": dt.date(2026, 10, 1),
        "period_end": dt.date(2026, 11, 1),
        "supplier_legal_name": "Synthetic Supplier Private Limited",
        "supplier_gstin": "37ABCDE1234F1Z5",
        "supplier_state_code": "37",
        "recipient_legal_name": "Synthetic Educational Society",
        "recipient_gstin": "37PQRSX6789K1Z2",
        "recipient_address": {
            "address_line1": "1 Synthetic Road",
            "address_line2": None,
            "city": "Vijayawada",
            "district": "NTR",
            "postal_code": "520001",
            "state_code": "37",
        },
        "place_of_supply_state_code": "37",
        "tax_type": "cgst_sgst",
        "taxable_value_inr": D("5000.00"),
        "cgst_inr": D("450.00"),
        "sgst_inr": D("450.00"),
        "igst_inr": D("0.00"),
        "total_inr": D("5900.00"),
        "notes": None,
    }
    row.update(overrides)
    return row


def _lines(rate: str = "18.00") -> list[dict[str, Any]]:
    return [
        {
            "line_no": 1,
            "kind": "subscription",
            "description": "SchoolOS Synthetic Standard (2026-10-01 to 2026-10-31)",
            "sac_code": "998314",
            "quantity": D("1.000"),
            "unit_price_inr": D("5000.00"),
            "amount_inr": D("5000.00"),
            "gst_rate": D(rate),
        }
    ]


def _html(**overrides: Any) -> str:
    rate = overrides.pop("rate", "18.00")
    doc = ip.build_document(
        _invoice(**overrides), _lines(rate), supplier_address=SUPPLIER_ADDRESS, cfg=ip.pdf_config()
    )
    return ip.render_invoice_html(doc)


def _text(page: str) -> str:
    """Visible text of the page (tags dropped, entities left as written)."""
    body = page.split("<body>", 1)[1]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def test_FR_PLT_017_template_v0_config_is_valid_and_versioned() -> None:
    cfg = ip.pdf_config()
    assert cfg.template_version == "v0"
    assert cfg.language == "en"
    assert cfg.object_prefix == "platform/invoices/"
    assert not cfg.object_prefix.startswith("t/")
    assert cfg.state_names["37"] == "Andhra Pradesh"
    assert cfg.download_url_ttl_s <= 300


def test_FR_PLT_017_rule_46_fields_intra_state() -> None:
    text = _text(_html())
    for expected in (
        "Tax Invoice",
        "SOS/26-27/000123",
        "Invoice date 01/10/2026",
        "Due date 16/10/2026",
        "01/10/2026 to 31/10/2026",
        "Synthetic Supplier Private Limited",
        "Door 1-2, Synthetic Road",
        "GSTIN: 37ABCDE1234F1Z5",
        "Synthetic Educational Society",
        "GSTIN: 37PQRSX6789K1Z2",
        "Vijayawada, NTR",
        "Andhra Pradesh - 520001",
        "Place of supply: 37 - Andhra Pradesh",
        "reverse charge: No",
        "998314",
        "Taxable value 5,000.00",
        "CGST @ 9% 450.00",
        "SGST @ 9% 450.00",
        "Total (INR) 5,900.00",
        "Indian Rupees Five Thousand Nine Hundred Only",
        "Authorised signatory",
    ):
        assert expected in text, expected
    assert "IGST" not in text
    assert "GST not charged" not in text


def test_FR_PLT_017_inter_state_shows_igst_only() -> None:
    text = _text(
        _html(
            place_of_supply_state_code="29",
            tax_type="igst",
            cgst_inr=D("0.00"),
            sgst_inr=D("0.00"),
            igst_inr=D("900.00"),
            recipient_gstin=None,
            recipient_address={
                "address_line1": "2 Synthetic Layout",
                "city": "Bengaluru",
                "postal_code": "560001",
                "state_code": "29",
            },
        )
    )
    assert "IGST @ 18% 900.00" in text
    assert "CGST" not in text
    assert "SGST" not in text
    assert "Place of supply: 29 - Karnataka" in text
    assert "GSTIN: Unregistered" in text


def test_docs_16_Q3_zero_rate_invoice_carries_the_note() -> None:
    text = _text(_html(rate="0", cgst_inr=D("0.00"), sgst_inr=D("0.00"), total_inr=D("5000.00")))
    assert "GST not charged" in text
    assert "CGST @ 0% 0.00" in text


def test_FR_PLT_016_drafts_have_no_pdf() -> None:
    with pytest.raises(ip.NotNumbered):
        ip.build_document(
            _invoice(invoice_number=None, issue_date=None),
            _lines(),
            supplier_address=SUPPLIER_ADDRESS,
            cfg=ip.pdf_config(),
        )


def test_SEC_017_every_value_is_escaped() -> None:
    page = _html(
        recipient_legal_name='<script>alert("x")</script> & Sons',
        notes="<img src=x onerror=alert(1)> PO 42",
    )
    assert "<script>" not in page
    assert "<img" not in page
    assert "&lt;script&gt;" in page
    assert "&amp; Sons" in page
    assert "&lt;img src=x onerror=alert(1)&gt; PO 42" in page


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_docs_07_10_page_references_no_url_but_the_bundled_font() -> None:
    page = _html(notes="see https://example.invalid/pay")
    urls = set(re.findall(r'url\("([^"]+)"\)', page))
    assert urls == {FONT_URL}
    assert "<link" not in page
    assert "<script" not in page
    assert "src=" not in page
    assert "@page { size: A4 portrait" in page


def test_ADR_0036_invoice_page_asks_for_no_font_while_telugu_is_hidden() -> None:
    page = _html(notes="see https://example.invalid/pay")
    assert set(re.findall(r'url\("([^"]+)"\)', page)) == set()
    assert "@font-face" not in page
    assert "Noto Sans Telugu" not in page
    assert not contains_telugu(page)
    assert "@page { size: A4 portrait" in page


def test_CLAUDE_11_no_student_fields_reach_the_template() -> None:
    """The document is built only from the invoice snapshot: supplier, billing entity, lines."""
    fields = set(ip.InvoiceDocument.__dataclass_fields__)
    assert not {f for f in fields if re.search(r"student|guardian|aadhaar|dob|roll", f)}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "0.00"),
        ("999.5", "999.50"),
        ("1000", "1,000.00"),
        ("100000", "1,00,000.00"),
        ("1234567.891", "12,34,567.89"),
        ("-100.00", "-100.00"),
        ("123456789.00", "12,34,56,789.00"),
    ],
)
def test_format_inr_indian_grouping(value: str, expected: str) -> None:
    assert ip.format_inr(D(value)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "Indian Rupees Zero Only"),
        ("5900.00", "Indian Rupees Five Thousand Nine Hundred Only"),
        ("1062.00", "Indian Rupees One Thousand Sixty Two Only"),
        ("14.76", "Indian Rupees Fourteen and Seventy Six Paise Only"),
        ("250000.05", "Indian Rupees Two Lakh Fifty Thousand and Five Paise Only"),
        (
            "12345678.00",
            "Indian Rupees One Crore Twenty Three Lakh Forty Five Thousand Six "
            "Hundred Seventy Eight Only",
        ),
    ],
)
def test_amount_in_words_indian_system(value: str, expected: str) -> None:
    assert ip.amount_in_words(D(value)) == expected


def test_pdf_filename_is_ascii_and_derived_from_the_number() -> None:
    assert ip.pdf_filename("SOS/26-27/000123") == "invoice-SOS-26-27-000123.pdf"


def test_ADR_0017_invoice_objects_never_use_a_school_prefix() -> None:
    iid, rid = uuid.uuid4(), uuid.uuid4()
    key = object_key("platform/invoices/", "2026-27", iid, rid)
    assert key == f"platform/invoices/2026-27/{iid}/{rid}.pdf"
    with pytest.raises(ValueError, match="control-plane prefix"):
        object_key("t/", "2026-27", iid, rid)
    store = MemoryInvoiceStore()
    with pytest.raises(ValueError, match="outside the control-plane prefix"):
        store.put(f"t/{uuid.uuid4()}/exports/x.pdf", b"%PDF-")
    with pytest.raises(ValueError, match=r"1..300"):
        store.presigned_get(key, filename="x.pdf", expires_s=301)


# --- real render (skipped without Chromium) ---------------------------------------------------


def _chromium_available() -> bool:
    from playwright.sync_api import Error, sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(chromium_sandbox=False)
            browser.close()
    except Error:
        return False
    return True


@pytest.mark.chromium
def test_FR_PLT_017_real_render_text_has_number_totals_and_gstins() -> None:
    if not _chromium_available():
        pytest.skip("headless Chromium is not installed on this machine")
    import pypdfium2 as pdfium

    out = ChromiumRenderer(sandbox=False, timeout_ms=60_000).render(_html())
    assert out.startswith(b"%PDF-")
    pdf = pdfium.PdfDocument(out)
    try:
        pages = len(pdf)
        text = " ".join(pdf[i].get_textpage().get_text_range() for i in range(pages))
    finally:
        pdf.close()
    text = re.sub(r"\s+", " ", text)
    assert pages == 1
    for expected in (
        "SOS/26-27/000123",
        "37ABCDE1234F1Z5",
        "37PQRSX6789K1Z2",
        "5,900.00",
        "5,000.00",
        "450.00",
        "998314",
        "Tax Invoice",
    ):
        assert expected in text, expected
