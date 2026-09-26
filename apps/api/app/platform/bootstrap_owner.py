"""First-deploy bootstrap: create the first active ``platform_owner`` (FR-PLT-028).

    python -m app.platform.bootstrap_owner --subject <operator-pool sub> \\
        --email founder@example.com --display-name "Founder"

Refuses when any active platform owner exists (later owners are invited through the panel,
with step-up). Runs as ``sos_platform`` (SOS_PLATFORM_DATABASE_URL); audited in the platform
chain as a system action. Prints only the new operator ID.
"""

from __future__ import annotations

import argparse
import sys

from app.core.errors import DomainError
from app.platform import operators
from app.platform.schemas import OperatorInvite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", required=True, help="IdP subject (sub) in the operator pool")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args(argv)
    try:
        # Reuse the invite schema for validation of email/subject/name.
        data = OperatorInvite(
            email=args.email,
            display_name=args.display_name,
            idp_subject=args.subject,
            roles=["platform_owner"],
        )
        operator_id = operators.bootstrap_owner(
            subject=data.idp_subject, email=data.email, display_name=data.display_name
        )
    except DomainError as exc:
        sys.stderr.write(f"refused: {exc.code}\n")
        return 1
    except ValueError:
        sys.stderr.write("refused: invalid input\n")
        return 2
    sys.stdout.write(f"{operator_id}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
