"""Structured logging setup.

The app had no logging configuration at all, so every logger.* call in the
services -- including the Groq error log in services/llm.py and the fetch
failures in services/web_search.py -- was discarded at the root logger's default
WARNING level or swallowed entirely under uvicorn's own config. Errors were
invisible.

One JSON object per line on stdout, so the same format works for local
development and for whatever log aggregator the deployment ends up using. Phase
0c adds per-request stage timings on top of this via the `extra=` channel.
"""

import json
import logging
import os
import sys

# LogRecord attributes that are always present. Anything else on the record came
# from an `extra={...}` argument and belongs in the output.
_BUILTIN_ATTRS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
}


# Third-party loggers that would otherwise write user text (see setup_logging).
QUIET_LOGGERS = ("primp", "ddgs")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _BUILTIN_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def setup_logging():
    """Install the JSON formatter on the root logger. Idempotent."""
    level = os.getenv("LOG_LEVEL", "INFO").upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    # force=True replaces any handler uvicorn or a previous call installed, so
    # calling this twice doesn't duplicate every line.
    logging.basicConfig(level=level, handlers=[handler], force=True)
    root.setLevel(level)

    # uvicorn installs its own handlers; let its records propagate to ours
    # instead so everything comes out in one format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # The search stack logs every request URL at INFO, and a search URL contains
    # the user's question (e.g. "response: https://...&search=How+do+I+apply...").
    # Our own logs carry a hash instead; keep these libraries at WARNING so the
    # question never reaches the log through them.
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
