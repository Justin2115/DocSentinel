"""Child-process entry so Surya/Torch native crashes do not kill the API.

One-shot: python -m app.services.surya_worker <image> <language>
Persistent: python -m app.services.surya_worker --serve
  then send JSON lines on stdin: {"image": "<path>", "language": "auto"}
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("TORCH_NUM_THREADS", "1")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s [surya_worker] %(name)s: %(message)s",
    stream=sys.stderr,
    force=True,
)
logger = logging.getLogger("surya_worker")

from PIL import Image


def _fail_payload(exc: BaseException) -> dict:
    tb = traceback.format_exc()
    logger.exception("Surya worker request failed")
    traceback.print_exc(file=sys.stderr)
    sys.stderr.flush()
    return {
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": tb,
        "text": "",
        "confidence": 0,
        "engine": "None",
        "language": "en",
    }


def _ocr_one(image_path: str, language: str) -> dict:
    from app.services.ocr_service import _run_surya_on_image

    logger.info("OCR start image=%s language=%s", image_path, language)
    with Image.open(image_path) as image:
        logger.info("Opened image size=%s mode=%s", image.size, image.mode)
        text, confidence, engine, detected = _run_surya_on_image(image, language)
    logger.info(
        "OCR done engine=%s language=%s confidence=%s chars=%s",
        engine,
        detected,
        confidence,
        len(text or ""),
    )
    return {
        "text": text,
        "confidence": confidence,
        "engine": engine,
        "language": detected,
    }


def _serve() -> None:
    from app.services.ocr_service import get_surya_runtime

    os.environ["DOC_SENTINEL_SURYA_CHILD"] = "1"
    logger.info("Loading Surya runtime in worker pid=%s", os.getpid())
    try:
        runtime = get_surya_runtime()
    except Exception as exc:
        payload = _fail_payload(exc)
        print(json.dumps(payload), flush=True)
        sys.exit(1)
    if runtime is None:
        logger.error("get_surya_runtime() returned None")
        print(json.dumps({"error": "surya_init_failed", "ready": False}), flush=True)
        sys.exit(1)
    logger.info("Surya runtime ready kind=%s", runtime.get("kind"))
    print(json.dumps({"ready": True, "kind": runtime.get("kind")}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line == '{"cmd": "quit"}':
            logger.info("Worker received quit")
            break
        try:
            payload = json.loads(line)
            result = _ocr_one(payload["image"], payload.get("language") or "auto")
            print(json.dumps(result), flush=True)
        except Exception as exc:
            print(json.dumps(_fail_payload(exc)), flush=True)


def main() -> None:
    try:
        if len(sys.argv) >= 2 and sys.argv[1] == "--serve":
            _serve()
            return

        if len(sys.argv) < 3:
            print(json.dumps({"error": "usage: surya_worker <image> <language>"}))
            sys.exit(2)

        image_path, language = sys.argv[1], sys.argv[2]
        print(json.dumps(_ocr_one(image_path, language)), flush=True)
    except Exception as exc:
        print(json.dumps(_fail_payload(exc)), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
