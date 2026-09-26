# ruff: noqa
# mypy: ignore-errors
# Test fixture for `semgrep --test .semgrep`. Never imported or executed. Synthetic values only.
import logging

from app.core.logging import get_logger

log = get_logger(__name__)
logger = logging.getLogger(__name__)
student_id = "0192f1c2-0000-7000-8000-000000000001"
name = "Synthetic Student"


class Service:
    def __init__(self) -> None:
        self._log = get_logger(__name__)

    def run(self) -> None:
        # ruleid: sos-log-message-not-constant
        self._log.info(f"created {name}")
        # ok: sos-log-message-not-constant
        self._log.info("student.created", student_id=student_id)


# ruleid: sos-log-message-not-constant
log.info(f"student {name} created")

# ruleid: sos-log-message-not-constant
logger.warning("student %s missing DOB" % name)

# ruleid: sos-log-message-not-constant
log.error("import failed for {}".format(name))

# ruleid: sos-log-message-not-constant
log.debug("student " + name)

# ruleid: sos-log-message-not-constant
logging.info(f"login by {name}")

# ruleid: sos-log-message-not-constant
get_logger(__name__).exception(f"boom {name}")

# ruleid: sos-log-message-not-constant
logger.log(logging.INFO, f"student {name}")

# ruleid: sos-log-message-not-constant
audit_log.critical(str(name))

# ok: sos-log-message-not-constant
log.info("student.created", student_id=student_id)

# ok: sos-log-message-not-constant
logger.warning("student %s missing DOB", student_id)

# ok: sos-log-message-not-constant
logger.log(logging.INFO, "import.finished", extra={"rows": 10})

# ok: sos-log-message-not-constant
catalog.info(f"not a logger {name}")

# ok: sos-log-message-not-constant
label = f"Student {name}"
