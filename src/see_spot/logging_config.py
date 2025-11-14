import sys
import logging
from logging.config import dictConfig


def setup_logging(level: str = "DEBUG") -> None:
    """Configure application and uvicorn logging in a single, idempotent place.

    Call this exactly once near process start (before creating the FastAPI app).
    Safe to call multiple times; subsequent calls are no-ops.
    """
    if getattr(setup_logging, "_configured", False):  # idempotent guard
        return

    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            },
            "access": {
                "format": "%(asctime)s | %(levelname)-8s | uvicorn.access | %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "default",
                "level": level,
            },
            "access_console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "access",
                "level": level,
            },
        },
        "loggers": {
            # Project package
            "see_spot": {"handlers": ["console"], "level": level, "propagate": False},
            # Uvicorn internals
            "uvicorn": {"handlers": ["console"], "level": level, "propagate": True},
            "uvicorn.error": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["access_console"], "level": level, "propagate": False},
            # FastAPI / Starlette
            "fastapi": {"handlers": ["console"], "level": level, "propagate": True},
        },
        "root": {"handlers": ["console"], "level": level},
    })

    setup_logging._configured = True
    logging.getLogger("see_spot.logging_config").debug("Logging configured (level=%s)", level)
