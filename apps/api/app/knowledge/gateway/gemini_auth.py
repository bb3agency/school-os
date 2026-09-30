"""OAuth access tokens for Vertex AI from a service identity, never a person (ADR-0033; inv. 10).

The only module that imports ``google.auth`` (semgrep ``sos-llm-sdk-outside-gateway``). Two
credential sources, chosen by ``SOS_LLM_GCP_CREDENTIALS_SOURCE`` and given as JSON in
``SOS_LLM_GCP_CREDENTIALS_JSON`` (injected from AWS Secrets Manager, never a file in the image):

- ``workload-identity`` (preferred): a Google *external account* configuration for AWS -> Google
  workload identity federation. It holds no secret: the AWS identity of the task or host (ECS task
  role, EC2 instance profile) is exchanged at Google STS for a short-lived token that impersonates
  the Vertex service account. The AWS credentials come from boto3's normal chain (so ECS task roles
  work; google-auth's own AWS lookup only knows EC2 metadata and environment keys).
- ``service-account-key``: a service-account JSON key held in Secrets Manager (rotate it at least
  every 90 days; prefer workload identity).

Anything else is refused, in particular ``authorized_user`` (a person's ``gcloud`` login, i.e. a
consumer/personal account) and API keys (Gemini Developer API keys are not accepted at all: the
gateway only speaks to Vertex AI with OAuth). The check is
:func:`app.core.config.gcp_credential_problem`, shared with the ``Settings`` start-up guard.

Token refresh goes through :class:`HttpxAuthRequest` (httpx, the repository's HTTP client) with
its own short timeout; tokens are cached by the credentials object and refreshed under a lock
shortly before they expire. Nothing here logs a token, a key or a response body.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from typing import Any, Final, Protocol

import boto3
import httpx
from google.auth import aws, exceptions
from google.auth import transport as google_transport
from google.oauth2 import service_account

from app.core.config import LlmCredentialsSource, gcp_credential_problem
from app.knowledge.gateway.transport import TransportError

SCOPES: Final = ("https://www.googleapis.com/auth/cloud-platform",)


class TokenSource(Protocol):
    def token(self) -> str:
        """A valid OAuth access token (refreshed when needed); raises :class:`TransportError`."""
        ...


class _Response(google_transport.Response):
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    @property
    def data(self) -> bytes:
        return self._response.content


class HttpxAuthRequest(google_transport.Request):
    """google-auth's HTTP callable over httpx (token exchange, STS, impersonation)."""

    def __init__(self, client: httpx.Client, timeout_s: float = 10.0) -> None:
        self._client = client
        self._timeout = timeout_s

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> _Response:
        try:
            response = self._client.request(
                method,
                url,
                content=body,
                headers=dict(headers or {}),
                timeout=timeout or self._timeout,
            )
        except httpx.HTTPError as exc:
            raise exceptions.TransportError("token request failed") from exc  # type: ignore[no-untyped-call]
        return _Response(response)


class _BotoAwsSupplier(aws.AwsSecurityCredentialsSupplier):
    """AWS credentials and region from boto3's chain (ECS task role, instance profile)."""

    def __init__(self, region: str) -> None:
        self._region = region

    def get_aws_security_credentials(
        self, context: Any, request: Any
    ) -> aws.AwsSecurityCredentials:
        found = boto3.session.Session().get_credentials()
        frozen = found.get_frozen_credentials() if found is not None else None
        if frozen is None or not frozen.access_key or not frozen.secret_key:
            raise exceptions.RefreshError(  # type: ignore[no-untyped-call]
                "no AWS identity for workload identity federation"
            )
        return aws.AwsSecurityCredentials(frozen.access_key, frozen.secret_key, frozen.token)

    def get_aws_region(self, context: Any, request: Any) -> str:
        return self._region


class GoogleTokenSource:
    """Service-identity access tokens for the ``cloud-platform`` scope."""

    def __init__(
        self, source: LlmCredentialsSource, raw: str, *, http: httpx.Client, aws_region: str
    ) -> None:
        problem = gcp_credential_problem(source, raw)
        if problem is not None:
            raise ValueError(f"SOS_LLM_GCP_CREDENTIALS_JSON {problem}")
        info = json.loads(raw)
        self._request = HttpxAuthRequest(http)
        self._lock = threading.Lock()
        self._credentials: Any
        if source is LlmCredentialsSource.SERVICE_ACCOUNT_KEY:
            self._credentials = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
                info, scopes=list(SCOPES)
            )
        else:
            self._credentials = aws.Credentials(  # type: ignore[no-untyped-call]
                audience=info["audience"],
                subject_token_type=info["subject_token_type"],
                token_url=info["token_url"],
                service_account_impersonation_url=info["service_account_impersonation_url"],
                aws_security_credentials_supplier=_BotoAwsSupplier(aws_region),
                scopes=list(SCOPES),
            )

    def token(self) -> str:
        with self._lock:
            if not self._credentials.valid:
                try:
                    self._credentials.refresh(self._request)
                except exceptions.TransportError:
                    raise TransportError("connection") from None
                except exceptions.GoogleAuthError:
                    raise TransportError("rejected") from None
            token = self._credentials.token
        if not isinstance(token, str) or not token:
            raise TransportError("rejected")
        return token


class StaticTokenSource:
    """A fixed token (tests and the recorded-fixture contract tests only)."""

    def __init__(self, token: str) -> None:
        self._token = token

    def token(self) -> str:
        return self._token


__all__ = [
    "SCOPES",
    "GoogleTokenSource",
    "HttpxAuthRequest",
    "StaticTokenSource",
    "TokenSource",
]
