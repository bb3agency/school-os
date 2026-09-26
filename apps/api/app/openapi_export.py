"""Print the OpenAPI document (``make openapi``). Built in shared mode so every route appears."""

from __future__ import annotations

import json
import sys

from app.core.config import DeploymentMode, Settings, get_settings
from app.main import create_app


def document() -> str:
    """Stable, sorted JSON so diffs stay reviewable."""
    settings = Settings(**{**get_settings().model_dump(), "deployment_mode": DeploymentMode.SHARED})
    return json.dumps(create_app(settings).openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    sys.stdout.write(document())
