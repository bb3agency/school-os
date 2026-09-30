# ruff: noqa
# mypy: ignore-errors
# Test fixture for `semgrep --test .semgrep`. Never imported or executed.
import importlib

# ruleid: sos-llm-sdk-outside-gateway
import anthropic

# ruleid: sos-llm-sdk-outside-gateway
import openai as oa

# ruleid: sos-llm-sdk-outside-gateway
import anthropic.types

# ruleid: sos-llm-sdk-outside-gateway
from anthropic import Anthropic

# ruleid: sos-llm-sdk-outside-gateway
from anthropic.types import Message as Msg

# ruleid: sos-llm-sdk-outside-gateway
from voyageai import Client


def lazy() -> object:
    # ruleid: sos-llm-sdk-outside-gateway
    import openai

    return openai


# ruleid: sos-llm-sdk-outside-gateway
sdk = importlib.import_module("anthropic")

# ruleid: sos-llm-sdk-outside-gateway
sdk2 = __import__("voyageai.client")

# ruleid: sos-llm-sdk-outside-gateway
from google import genai

# ruleid: sos-llm-sdk-outside-gateway
import google.genai.types

# ruleid: sos-llm-sdk-outside-gateway
import vertexai

# ruleid: sos-llm-sdk-outside-gateway
from google.cloud import aiplatform

# ruleid: sos-llm-sdk-outside-gateway
from google.oauth2 import service_account

# ruleid: sos-llm-sdk-outside-gateway
import google.auth

# ok: sos-llm-sdk-outside-gateway
from google.protobuf import json_format

# ok: sos-llm-sdk-outside-gateway
from app.knowledge.gateway import service as gateway

# ok: sos-llm-sdk-outside-gateway
import anthropic_style_helpers

# ok: sos-llm-sdk-outside-gateway
from app.knowledge import openai_compat_notes

# ok: sos-llm-sdk-outside-gateway
provider_name = "anthropic"

# ok: sos-llm-sdk-outside-gateway
cfg = importlib.import_module("app.knowledge.gateway.service")

__all__ = ["Anthropic", "Client", "Msg", "anthropic", "gateway", "lazy", "oa"]
