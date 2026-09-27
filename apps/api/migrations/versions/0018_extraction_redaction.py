"""Extraction pages: redacted page images (PRV-016, ADR-0007; docs/05 §5.4).

A register page whose text showed a full Aadhaar number (``aadhaar_detected``) used to be
withheld until image redaction existed. Now its image is either redacted (the number regions
blacked out; the page points at the redacted document version and can be shown and used as
evidence) or, when that is not possible, the original is discarded and the page stays
withheld. ``image_redacted`` records the first outcome; exactly one of ``image_withheld`` and
``image_redacted`` holds for a detected page and neither for any other page.

Expand-only: the new column defaults to false and code that predates it (withheld iff detected)
satisfies the new CHECK.

Downgrade restores the previous CHECK and drops the column. It is irreversible once a page has
been redacted: 0017 cannot represent a shown page that showed a number, and the migrator cannot
rewrite those rows (FORCE RLS hides tenant rows from the table owner, docs/05 §13), so the
downgrade stops with an explanation instead (CHECK validation sees every row). With no redacted
pages it round-trips cleanly.

Revision ID: 0018_extraction_redaction
Revises: 0017_exports
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0018_extraction_redaction"
down_revision = "0017_exports"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE sis.extraction_pages
  ADD COLUMN image_redacted boolean NOT NULL DEFAULT false;
ALTER TABLE sis.extraction_pages DROP CONSTRAINT extraction_pages_withhold_detected;
-- PRV-016: a page whose text held a full Aadhaar number is shown only as a redacted copy.
ALTER TABLE sis.extraction_pages ADD CONSTRAINT extraction_pages_image_state
  CHECK (image_withheld::int + image_redacted::int = aadhaar_detected::int);
"""

DOWNGRADE_SQL = r"""
ALTER TABLE sis.extraction_pages DROP CONSTRAINT extraction_pages_image_state;
DO $$
BEGIN
  ALTER TABLE sis.extraction_pages ADD CONSTRAINT extraction_pages_withhold_detected
    CHECK (NOT aadhaar_detected OR image_withheld);
EXCEPTION WHEN check_violation THEN
  RAISE EXCEPTION 'irreversible: redacted register pages exist (PRV-016)'
    USING ERRCODE = 'check_violation',
          HINT = '0017_exports cannot represent them; see the 0018_extraction_redaction docstring.';
END
$$;
ALTER TABLE sis.extraction_pages DROP COLUMN image_redacted;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
