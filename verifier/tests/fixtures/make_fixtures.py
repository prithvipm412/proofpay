"""Make the small fixture images for the verifier tests, then MEASURE their pHash distances.

Run from verifier/:  .venv/bin/python tests/fixtures/make_fixtures.py
The images are deterministic (fixed random seeds). The measured distances are written to
distances.json; tests read that file and check it against a fresh measurement (V-T4: do not
invent fixture distances).
"""

import json
import pathlib
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from app.images import canonical_image, distance, phash_hex  # noqa: E402


def scene(seed: int, litter: bool, size=(640, 480)) -> Image.Image:
    """A textured synthetic 'photo': sky, ground, a bench, optional litter. Drawn at 640x480, saved at half size."""
    rng = np.random.default_rng(seed)
    w, h = size
    base = np.zeros((h, w, 3), dtype=np.float32)
    sky = np.array(rng.integers(90, 200, 3), dtype=np.float32)
    ground = np.array(rng.integers(40, 140, 3), dtype=np.float32)
    horizon = int(h * rng.uniform(0.45, 0.65))
    base[:horizon] = sky
    base[horizon:] = ground
    base += rng.normal(0, 6, base.shape)  # sensor noise
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img)
    for _ in range(6):  # background shapes (trees, buildings) fixed by the seed
        x, y = int(rng.integers(0, w - 80)), int(rng.integers(0, horizon - 40))
        col = tuple(int(c) for c in rng.integers(0, 255, 3))
        d.rectangle((x, y, x + int(rng.integers(30, 120)), y + int(rng.integers(30, 160))), fill=col)
    bx = int(rng.integers(100, w - 300))
    d.rectangle((bx, horizon - 60, bx + 240, horizon - 40), fill=(110, 70, 40))
    d.rectangle((bx, horizon - 20, bx + 240, horizon), fill=(110, 70, 40))
    if litter:
        lr = np.random.default_rng(seed + 1000)
        for _ in range(25):
            x, y = int(lr.integers(0, w - 30)), int(lr.integers(horizon + 5, h - 20))
            col = tuple(int(c) for c in lr.integers(150, 255, 3))
            d.rectangle((x, y, x + int(lr.integers(10, 40)), y + int(lr.integers(8, 25))), fill=col)
    return img.resize((w // 2, h // 2), Image.Resampling.LANCZOS)


def save_jpeg(img: Image.Image, path: pathlib.Path, quality=90, exif=None) -> None:
    kwargs = {"quality": quality}
    if exif is not None:
        kwargs["exif"] = exif
    img.save(path, format="JPEG", **kwargs)


def with_capture_time(dto: str, offset: str | None) -> Image.Exif:
    exif = Image.Exif()
    ifd = exif.get_ifd(0x8769)
    ifd[36867] = dto  # DateTimeOriginal
    if offset:
        ifd[36881] = offset  # OffsetTimeOriginal
    return exif


def main() -> None:
    a_before, a_after = scene(1, True), scene(1, False)
    b_before, b_after = scene(2, True), scene(2, False)
    save_jpeg(a_before, HERE / "a_before.jpg")
    save_jpeg(a_after, HERE / "a_after.jpg", exif=with_capture_time("2026:10:05 10:00:00", "+05:30"))
    save_jpeg(b_before, HERE / "b_before.jpg")
    save_jpeg(b_after, HERE / "b_after.jpg", exif=with_capture_time("2026:10:05 10:00:00", None))

    # Transformed copy of a_after: resize 50% and JPEG quality 60 (a near-duplicate).
    small = a_after.resize((a_after.width // 2, a_after.height // 2), Image.Resampling.LANCZOS)
    save_jpeg(small, HERE / "a_after_half_q60.jpg", quality=60)

    # PNG with transparency (canonical image test) and the same image saved as JPEG bytes.
    rgba = a_after.convert("RGBA")
    rgba.putalpha(255)
    ImageDraw.Draw(rgba).rectangle((0, 0, 100, 100), fill=(0, 0, 0, 0))
    rgba.save(HERE / "transparent.png")

    # Animated images (V-L4).
    frames = [scene(3, False).resize((64, 48)), scene(4, False).resize((64, 48))]
    frames[0].save(HERE / "animated.png", save_all=True, append_images=frames[1:], duration=100)
    frames[0].save(HERE / "animated.webp", save_all=True, append_images=frames[1:], duration=100)

    def ph(name: str) -> str:
        with Image.open(HERE / name) as im:
            return phash_hex(canonical_image(im))

    pairs = {
        "a_before__a_after": ("a_before.jpg", "a_after.jpg"),
        "b_before__b_after": ("b_before.jpg", "b_after.jpg"),
        "a_after__b_after": ("a_after.jpg", "b_after.jpg"),
        "a_after__a_after_half_q60": ("a_after.jpg", "a_after_half_q60.jpg"),
        "a_after__transparent": ("a_after.jpg", "transparent.png"),
    }
    measured = {k: distance(ph(x), ph(y)) for k, (x, y) in pairs.items()}
    (HERE / "distances.json").write_text(json.dumps(measured, indent=2) + "\n")
    for k, v in measured.items():
        print(f"{k:32s} {v}")
    for p in sorted(HERE.glob("*.*")):
        if p.suffix in (".jpg", ".png", ".webp"):
            print(f"{p.name:24s} {p.stat().st_size:7d} bytes")


if __name__ == "__main__":
    main()
