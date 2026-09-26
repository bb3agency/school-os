"""Cognito pre-token-generation trigger (event version V2_0) for SchoolOS (ADR-0018, SEC-005).

Adds the custom claim ``sos:mfa`` to access and ID tokens: ``"true"`` when the user has an MFA method
enabled, else ``"false"``. The staff pool has device remembering off and adaptive authentication never
skips MFA, so a user with MFA enabled always completed an MFA challenge for this session. The API
refuses privileged memberships (owner, principal, office_admin) without ``sos:mfa == "true"`` and uses
``auth_time`` for step-up freshness.

No personal data is logged: only the outcome and the trigger source.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_idp = boto3.client("cognito-idp")

CLAIM = "sos:mfa"


def _mfa_enabled(user_pool_id: str, username: str) -> bool:
    user = _idp.admin_get_user(UserPoolId=user_pool_id, Username=username)
    return bool(user.get("UserMFASettingList"))


def handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    mfa = _mfa_enabled(event["userPoolId"], event["userName"])
    value = "true" if mfa else "false"
    event.setdefault("response", {})["claimsAndScopeOverrideDetails"] = {
        "accessTokenGeneration": {"claimsToAddOrOverride": {CLAIM: value}},
        "idTokenGeneration": {"claimsToAddOrOverride": {CLAIM: value}},
    }
    logger.info(
        json.dumps(
            {"event": "pre_token_generation", "trigger": event.get("triggerSource"), "mfa": mfa}
        )
    )
    return event
