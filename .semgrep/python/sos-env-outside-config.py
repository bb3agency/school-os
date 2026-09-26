# ruff: noqa
# mypy: ignore-errors
# Test fixture for `semgrep --test .semgrep`. Never imported or executed.
import os
from os import environ, getenv

from app.core.config import get_settings

# ruleid: sos-env-outside-config
db_url = os.environ["SOS_DATABASE_URL"]

# ruleid: sos-env-outside-config
level = os.environ.get("SOS_LOG_LEVEL", "INFO")

# ruleid: sos-env-outside-config
key = os.getenv("SOS_SERVICE_TOKEN_KEY")

# ruleid: sos-env-outside-config
os.environ["SOS_ENV"] = "local"

# ruleid: sos-env-outside-config
region = environ["AWS_REGION"]

# ruleid: sos-env-outside-config
mode = getenv("SOS_DEPLOYMENT_MODE")

# ruleid: sos-env-outside-config
os.putenv("SOS_ENV", "ci")

# ok: sos-env-outside-config
settings = get_settings()

# ok: sos-env-outside-config
bucket = settings.s3_bucket_files

# ok: sos-env-outside-config
path = os.path.join("a", "b")
