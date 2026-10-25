import json
import logging
import sys
from datetime import UTC, datetime

from app.core.security import redact


class RedactingFormatter(logging.Formatter):
    def __init__(self, as_json: bool):
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        self.as_json = as_json

    def format(self, record: logging.LogRecord) -> str:
        if self.as_json:
            payload = {
                "ts": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                payload["exc"] = self.formatException(record.exc_info)
            return redact(json.dumps(payload))
        return redact(super().format(record))


def configure_logging(level: str = "INFO", as_json: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter(as_json))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
