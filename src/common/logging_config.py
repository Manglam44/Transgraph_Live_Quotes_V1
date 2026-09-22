from __future__ import annotations

import json
import logging
import sys
import time

from src.common.settings import LOG_JSON, LOG_LEVEL


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(record.created)),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        if record.exc_info:
            payload['exc_info'] = self.formatException(record.exc_info)
        extra = getattr(record, 'extra_fields', None)
        if extra:
            payload.update(extra)
        return json.dumps(payload)


def configure_logging(service_name: str) -> logging.Logger:
    """Call once per process entrypoint. Returns a logger for that service."""
    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)

    # Avoid duplicate handlers if configure_logging is called more than once
    # (e.g. under a test runner or reload).
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if LOG_JSON:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt='%(asctime)s | %(levelname)-7s | %(name)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S',
            )
        )
    root.addHandler(handler)

    # ib_insync and psycopg2 are noisy at DEBUG; keep them at INFO+ unless
    # LOG_LEVEL itself is set to DEBUG explicitly.
    if LOG_LEVEL.upper() != 'DEBUG':
        logging.getLogger('ib_insync').setLevel(logging.WARNING)

    return logging.getLogger(service_name)
