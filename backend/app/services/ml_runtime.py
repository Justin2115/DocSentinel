"""Import-order guard for the two native ML runtimes used in one process.

On Windows, importing torch after PaddlePaddle has been initialised raises
OSError [WinError 127] ("Error loading torch/lib/shm.dll"), and every later
attempt fails the same way for the life of the process. That silently kills
embedding indexing for any document whose OCR ran first, so the document stays
searchable-free forever. Loading torch before Paddle makes both runtimes
coexist, so every Paddle entry point goes through this module first.
"""

import logging
import threading
import time

logger = logging.getLogger("docsentinel.ml_runtime")

_lock = threading.Lock()
_state = "pending"  # pending | loaded | unavailable


def ensure_torch_loaded() -> bool:
    """Import torch once, before any Paddle runtime is created.

    Returns True when torch is importable. A failed import is remembered so a
    broken environment does not pay the import cost on every OCR page.
    """
    global _state
    if _state != "pending":
        return _state == "loaded"
    with _lock:
        if _state != "pending":
            return _state == "loaded"
        t0 = time.perf_counter()
        try:
            import torch  # noqa: F401
        except Exception as exc:
            _state = "unavailable"
            logger.warning(
                "stage=ml.torch_import duration_s=%.3f status=failed error=%s",
                time.perf_counter() - t0,
                exc,
            )
            return False
        _state = "loaded"
        logger.info(
            "stage=ml.torch_import duration_s=%.3f status=ok note=cold_init",
            time.perf_counter() - t0,
        )
        return True
