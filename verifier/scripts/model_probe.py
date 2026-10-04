"""M0 model probe: send one image and one text to each candidate Qwen model ID.

Usage (from the verifier/ folder):
    .venv/bin/python scripts/model_probe.py <model-id> [<model-id> ...] [--image path/to/photo.jpg]

Reads DASHSCOPE_API_KEY and DASHSCOPE_BASE_URL from verifier/.env (or the shell environment).
It never prints the API key. Without --image it draws a red circle and a blue square.
A model ID "works" if the reply describes the image correctly.
"""

import argparse
import base64
import io
import os
import pathlib
import sys
import time

from openai import APIStatusError, OpenAI
from PIL import Image, ImageDraw

ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / ".env"
PROMPT = "Describe the shapes and their colors in this image in one short sentence."


def load_env(path: pathlib.Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding existing values."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def test_image_bytes() -> bytes:
    img = Image.new("RGB", (512, 512), "white")
    draw = ImageDraw.Draw(img)
    draw.ellipse((60, 60, 220, 220), fill="red")
    draw.rectangle((290, 290, 450, 450), fill="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def file_image_bytes(path: str) -> bytes:
    img = Image.open(path)
    img = img.convert("RGB")
    img.thumbnail((1280, 1280))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def probe(client: OpenAI, model: str, image: bytes) -> bool:
    data_url = "data:image/jpeg;base64," + base64.b64encode(image).decode()
    start = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=100,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
    except APIStatusError as exc:
        print(f"[FAIL] {model}: HTTP {exc.status_code} ({type(exc).__name__})")
        return False
    except Exception as exc:  # connection error, timeout
        print(f"[FAIL] {model}: {type(exc).__name__}")
        return False
    elapsed = time.monotonic() - start
    text = (resp.choices[0].message.content or "").strip()
    print(f"[OK]   {model}: {elapsed:.1f}s, returned model={resp.model!r}")
    print(f"       reply: {text}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="+", help="candidate model IDs")
    parser.add_argument("--image", help="optional photo to send instead of the drawn test image")
    args = parser.parse_args()

    load_env(ENV_FILE)
    base_url = os.environ.get("DASHSCOPE_BASE_URL", "")
    if not os.environ.get("DASHSCOPE_API_KEY") or not base_url:
        print("Set DASHSCOPE_API_KEY and DASHSCOPE_BASE_URL in verifier/.env first.")
        return 2

    print(f"base_url: {base_url}")
    client = OpenAI(api_key=os.environ["DASHSCOPE_API_KEY"], base_url=base_url, max_retries=0, timeout=60)
    image = file_image_bytes(args.image) if args.image else test_image_bytes()
    results = [probe(client, m, image) for m in args.models]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
