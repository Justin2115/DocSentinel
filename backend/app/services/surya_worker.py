"""Child-process entry so Surya/Torch native crashes do not kill the API."""

from __future__ import annotations

import json
import sys

from PIL import Image


def main() -> None:
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: surya_worker <image> <language>"}))
        sys.exit(2)

    image_path, language = sys.argv[1], sys.argv[2]
    from app.services.ocr_service import _run_surya_on_image

    with Image.open(image_path) as image:
        text, confidence, engine, detected = _run_surya_on_image(image, language)

    print(
        json.dumps(
            {
                "text": text,
                "confidence": confidence,
                "engine": engine,
                "language": detected,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
