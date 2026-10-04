"""V-L (input limits), V-P (preprocessing) and the V-L5 child process part of V-T12."""

import io
import multiprocessing
import os
import pathlib
import threading
import time

import pytest
from PIL import Image

from app import images
from app.images import (
    ImageRejected,
    canonical_image,
    capture_time,
    decode_file,
    decode_in_child,
    distance,
    free_slots,
    model_jpeg,
    phash_hex,
    preview_jpeg,
)
from tests.conftest import DISTANCES, FIXTURES


def _sleepy_child(src, preview_path, conn):
    """A decode that never ends (V-T12: decode that runs too long). Top level for spawn.
    It writes its PID to preview_path so the test can check that it was reaped."""
    if preview_path:
        pathlib.Path(preview_path).write_text(str(os.getpid()))
    time.sleep(60)


def _crash_child(src, preview_path, conn):
    raise SystemExit(3)


def write(tmp_path: pathlib.Path, name: str, data: bytes) -> pathlib.Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def png_bytes(size, mode="RGB", color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- V-L2..V-L4


def test_VT12_invalid_bytes_named_jpg_rejected(tmp_path):
    with pytest.raises(ImageRejected):
        decode_file(str(write(tmp_path, "photo.jpg", b"this is not an image" * 100)))


def test_VT12_png_named_jpg_is_png(tmp_path):
    info = decode_file(str(write(tmp_path, "photo.jpg", png_bytes((40, 30)))))
    assert (info["format"], info["ext"]) == ("PNG", "png")


@pytest.mark.parametrize("name", ["animated.png", "animated.webp"])
def test_VT12_animated_rejected(name):
    with pytest.raises(ImageRejected, match="more than one frame"):
        decode_file(str(FIXTURES / name))


def test_VT12_decompression_bomb_rejected(tmp_path):
    # 5001 x 5001 = 25,010,001 pixels: just above MAX_IMAGE_PIXELS, so Pillow gives only a
    # DecompressionBombWarning, which V-L2 turns into an error. Each side is below 8000.
    p = write(tmp_path, "bomb.png", png_bytes((5001, 5001), "L", 0))
    assert p.stat().st_size < 1_000_000
    with pytest.raises(ImageRejected, match="too many pixels"):
        decode_file(str(p))


def test_VT12_side_longer_than_8000_rejected(tmp_path):
    with pytest.raises(ImageRejected, match="8000"):
        decode_file(str(write(tmp_path, "wide.png", png_bytes((8001, 10)))))


def test_side_8000_accepted(tmp_path):
    assert decode_file(str(write(tmp_path, "wide.png", png_bytes((8000, 10)))))["width"] == 8000


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_VL3_other_formats_rejected(tmp_path, fmt):
    buf = io.BytesIO()
    Image.new("RGB", (20, 20)).save(buf, format=fmt)
    with pytest.raises(ImageRejected):
        decode_file(str(write(tmp_path, "x.jpg", buf.getvalue())))


def test_truncated_jpeg_rejected(tmp_path):
    data = (FIXTURES / "a_after.jpg").read_bytes()
    with pytest.raises(ImageRejected):
        decode_file(str(write(tmp_path, "cut.jpg", data[: len(data) // 2])))


def test_webp_accepted(tmp_path):
    buf = io.BytesIO()
    Image.open(FIXTURES / "a_after.jpg").save(buf, format="WEBP")
    assert decode_file(str(write(tmp_path, "x.bin", buf.getvalue())))["ext"] == "webp"


# ---------------------------------------------------------------- V-L5 child process


def test_VL5_decode_in_child_matches_in_process():
    assert decode_in_child(FIXTURES / "a_after.jpg") == decode_file(str(FIXTURES / "a_after.jpg"))
    assert free_slots() == images.DECODE_SLOTS


def test_VT12_slow_decode_stopped_and_slot_freed_after_child_ended(tmp_path):
    pid_file = tmp_path / "pid"
    started = time.monotonic()
    with pytest.raises(ImageRejected, match="too long"):
        decode_in_child(FIXTURES / "a_after.jpg", pid_file, timeout=1.5, target=_sleepy_child)
    assert time.monotonic() - started < 10
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):  # stopped AND reaped: no process (not even a zombie)
        os.kill(pid, 0)
    assert multiprocessing.active_children() == []
    assert free_slots() == images.DECODE_SLOTS


def test_VL5_crashed_child_is_rejected_not_timeout():
    with pytest.raises(ImageRejected, match="without a result"):
        decode_in_child(FIXTURES / "a_after.jpg", target=_crash_child)
    assert free_slots() == images.DECODE_SLOTS


def test_VL5_at_most_two_children_at_once():
    """A third decode waits until a slot frees."""
    errors = []

    def slow():
        try:
            decode_in_child(FIXTURES / "a_after.jpg", timeout=1.5, target=_sleepy_child)
        except ImageRejected as exc:
            errors.append(exc)

    threads = [threading.Thread(target=slow) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.5)
    assert free_slots() == 0
    started = time.monotonic()
    decode_in_child(FIXTURES / "a_after.jpg")  # waits for a slot
    waited = time.monotonic() - started
    for t in threads:
        t.join()
    assert waited >= 0.5
    assert len(errors) == 2
    assert free_slots() == images.DECODE_SLOTS


# ---------------------------------------------------------------- V-P


def test_VP1_transparent_pixels_on_white():
    with Image.open(FIXTURES / "transparent.png") as im:
        canon = canonical_image(im)
    assert canon.mode == "RGB"
    assert canon.getpixel((5, 5)) == (255, 255, 255)


def test_VP1_exif_rotation_applied(tmp_path):
    img = Image.new("RGB", (40, 20), (200, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW
    p = tmp_path / "rot.jpg"
    img.save(p, exif=exif)
    with Image.open(p) as im:
        assert canonical_image(im).size == (20, 40)


def test_VP2_phash_is_64_bit_hex():
    with Image.open(FIXTURES / "a_after.jpg") as im:
        ph = phash_hex(canonical_image(im))
    assert len(ph) == 16 and int(ph, 16) >= 0


def test_VT4_fixture_distances_are_measured_values():
    """The recorded distances must equal a fresh measurement of the fixture files."""

    def ph(name):
        with Image.open(FIXTURES / name) as im:
            return phash_hex(canonical_image(im))

    assert distance(ph("a_after.jpg"), ph("a_after_half_q60.jpg")) == DISTANCES["a_after__a_after_half_q60"]
    assert distance(ph("a_before.jpg"), ph("a_after.jpg")) == DISTANCES["a_before__a_after"]
    assert distance(ph("a_after.jpg"), ph("b_after.jpg")) == DISTANCES["a_after__b_after"]


def test_VP3_model_input_size():
    big = Image.new("RGB", (4000, 3000), (1, 2, 3))
    with Image.open(io.BytesIO(model_jpeg(big))) as im:
        assert im.format == "JPEG" and max(im.size) == 1280


def test_VP4_preview_has_no_metadata():
    with Image.open(FIXTURES / "a_after.jpg") as im:
        assert im.getexif()  # the fixture has EXIF
        data = preview_jpeg(canonical_image(im))
    with Image.open(io.BytesIO(data)) as im:
        assert im.format == "JPEG"
        assert not im.getexif()
        assert "icc_profile" not in im.info
    big = Image.new("RGB", (3000, 2000))
    with Image.open(io.BytesIO(preview_jpeg(big))) as im:
        assert max(im.size) == 1600


def test_VP5_capture_time_with_offset_is_utc():
    with Image.open(FIXTURES / "a_after.jpg") as im:
        assert capture_time(im) == ("2026-10-05T04:30:00Z", "timezone_specified")


def test_VP5_capture_time_without_offset():
    with Image.open(FIXTURES / "b_after.jpg") as im:
        assert capture_time(im) == ("2026-10-05T10:00:00", "no_timezone")


def test_VP5_no_capture_time():
    with Image.open(FIXTURES / "a_before.jpg") as im:
        assert capture_time(im) == (None, "none")
