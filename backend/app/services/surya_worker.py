"""Child-process entry so Surya/Torch native crashes do not kill the API.

One-shot: python -m app.services.surya_worker <image> <language>
Persistent: python -m app.services.surya_worker --serve
  then send JSON lines on stdin: {"image": "<path>", "language": "auto"}
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("TORCH_NUM_THREADS", "1")

from PIL import Image


def _ocr_one(image_path: str, language: str) -> dict:
    from app.services.ocr_service import _run_surya_on_image

    with Image.open(image_path) as image:
        text, confidence, engine, detected = _run_surya_on_image(image, language)
    return {
        "text": text,
        "confidence": confidence,
        "engine": engine,
        "language": detected,
    }


def _serve() -> None:
    from app.services.ocr_service import get_surya_runtime

    os.environ["DOC_SENTINEL_SURYA_CHILD"] = "1"
    runtime = get_surya_runtime()
    if runtime is None:
        print(json.dumps({"error": "surya_init_failed"}), flush=True)
        sys.exit(1)
    print(json.dumps({"ready": True}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line == '{"cmd": "quit"}':
            break
        try:
            payload = json.loads(line)
            result = _ocr_one(payload["image"], payload.get("language") or "auto")
            print(json.dumps(result), flush=True)
        except Exception as exc:
            print(json.dumps({"error": str(exc), "text": "", "confidence": 0, "engine": "None", "language": "en"}), flush=True)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--serve":
        _serve()
        return

    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: surya_worker <image> <language>"}))
        sys.exit(2)

    image_path, language = sys.argv[1], sys.argv[2]
    print(json.dumps(_ocr_one(image_path, language)), flush=True)


if __name__ == "__main__":
    main()
