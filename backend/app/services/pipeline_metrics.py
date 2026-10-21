"""Structured per-stage timing for the document processing pipeline.

Logs machine-readable `stage=... duration_s=...` lines without document
contents so processing latency can be attributed to specific stages.
"""

import logging
import time
from contextlib import contextmanager

logger = logging.getLogger("docsentinel.metrics")


@contextmanager
def stage(name: str, **fields):
    """Time a pipeline stage.

    Extra measurements can be attached via the mutable payload dict yielded:

        with stage("ocr.page", page=3) as payload:
            payload["engine"] = "PaddleOCR"
    """
    t0 = time.perf_counter()
    payload: dict[str, object] = {}
    try:
        yield payload
    except Exception as exc:
        duration = time.perf_counter() - t0
        logger.info(
            "stage=%s duration_s=%.3f status=failed error=%s %s",
            name,
            duration,
            type(exc).__name__,
            " ".join(f"{k}={v}" for k, v in fields.items()),
        )
        raise
    else:
        duration = time.perf_counter() - t0
        extra = " ".join(f"{k}={v}" for k, v in payload.items())
        fields_str = " ".join(f"{k}={v}" for k, v in fields.items())
        logger.info(
            "stage=%s duration_s=%.3f status=ok %s %s",
            name,
            duration,
            fields_str,
            extra,
        )
