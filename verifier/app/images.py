"""Input limits (V-L1..V-L5) and preprocessing (V-P1..V-P5).

Decoding of untrusted bytes happens only in a child process (V-L5). The child is started with the
"spawn" method on macOS and Linux (section 3.1); its target functions are at module top level.
Heavy imports (imagehash, numpy, scipy) are inside functions, so a child starts quickly.
"""

from __future__ import annotations

import hashlib
import io
import multiprocessing
import os
import pathlib
import re
import threading
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Callable

from PIL import ExifTags, Image, ImageOps

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # V-L1: the file part may be 10 MB; one more byte gives 413
MULTIPART_OVERHEAD = 64 * 1024  # room for multipart headers and boundaries around the file part
MAX_PIXELS = 25_000_000  # V-L2
MAX_SIDE = 8000  # V-L4
DECODE_TIMEOUT_S = 10.0  # V-L5
DECODE_SLOTS = 2  # V-L5
MODEL_LONG_SIDE, MODEL_QUALITY = 1280, 85  # V-P3
PREVIEW_LONG_SIDE, PREVIEW_QUALITY = 1600, 85  # V-P4

# V-L3: decoded format -> stored extension. Only these formats are accepted.
FORMAT_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
EXTENSIONS = tuple(FORMAT_EXT.values())


class ImageRejected(Exception):
    """The bytes are not an accepted image (HTTP 400)."""


class UploadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------- preprocessing (V-P)


def canonical_image(img: Image.Image) -> Image.Image:
    """V-P1: the one canonical image. EXIF rotation, transparent pixels on white, RGB."""
    img = ImageOps.exif_transpose(img)
    if img.has_transparency_data:
        rgba = img.convert("RGBA")
        white = Image.new("RGB", rgba.size, (255, 255, 255))
        white.paste(rgba, mask=rgba.getchannel("A"))
        return white
    return img.convert("RGB") if img.mode != "RGB" else img.copy()


def phash_hex(canonical: Image.Image) -> str:
    """64-bit pHash (hash size 8) of the canonical image, as 16 hex characters (V-P2)."""
    import imagehash

    return str(imagehash.phash(canonical, hash_size=8))


def distance(a: str, b: str) -> int:
    """Hamming distance between two pHash hex values."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _jpeg(canonical: Image.Image, long_side: int, quality: int) -> bytes:
    img = canonical.copy()
    img.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)  # no exif/icc arguments: no metadata
    return buf.getvalue()


def model_jpeg(canonical: Image.Image) -> bytes:
    """V-P3: model input."""
    return _jpeg(canonical, MODEL_LONG_SIDE, MODEL_QUALITY)


def preview_jpeg(canonical: Image.Image) -> bytes:
    """V-P4: public preview, no metadata."""
    return _jpeg(canonical, PREVIEW_LONG_SIDE, PREVIEW_QUALITY)


_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def capture_time(img: Image.Image) -> tuple[str | None, str]:
    """V-P5: (time, status). status is timezone_specified, no_timezone or none.

    With a time zone the value is UTC ISO with "Z". Without one it is the camera's local time,
    ISO without a zone. EXIF is not authenticated; it is only a soft signal (V-C6).
    """
    try:
        ifd = img.getexif().get_ifd(ExifTags.IFD.Exif)
        raw = ifd.get(ExifTags.Base.DateTimeOriginal)
        if not raw:
            return None, "none"
        local = datetime.strptime(str(raw).strip("\x00 ").strip(), "%Y:%m:%d %H:%M:%S")
        offset = str(ifd.get(ExifTags.Base.OffsetTimeOriginal) or "").strip("\x00 ").strip()
        m = _OFFSET.match(offset)
        if not m:
            return local.isoformat(), "no_timezone"
        sign = 1 if m.group(1) == "+" else -1
        tz = timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))
        utc = local.replace(tzinfo=tz).astimezone(timezone.utc)
        return utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "timezone_specified"
    except Exception:
        return None, "none"


def decode_file(src: str, preview_path: str | None = None) -> dict:
    """Decode and validate one stored file (V-L2..V-L4). Runs inside the child process."""
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # V-L2
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)  # V-L2
        try:
            img = Image.open(src, formats=tuple(FORMAT_EXT))
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ImageRejected("Image has too many pixels")
        except Exception:
            raise ImageRejected("File is not a JPEG, PNG or WebP image")
        with img:
            fmt = img.format
            if fmt not in FORMAT_EXT:  # V-L3 (for example MPO, a multi-picture JPEG)
                raise ImageRejected("File is not a JPEG, PNG or WebP image")
            if getattr(img, "n_frames", 1) != 1 or getattr(img, "is_animated", False):  # V-L4
                raise ImageRejected("Image has more than one frame")
            width, height = img.size
            if max(width, height) > MAX_SIDE:  # V-L4
                raise ImageRejected("Image side is longer than 8000 pixels")
            try:
                img.load()
            except Exception:
                raise ImageRejected("Image data could not be decoded")
            exif_time, exif_status = capture_time(img)
            canon = canonical_image(img)
    result = {
        "format": fmt,
        "ext": FORMAT_EXT[fmt],
        "width": width,
        "height": height,
        "phash": phash_hex(canon),
        "exif_time_utc": exif_time,
        "exif_time_status": exif_status,
    }
    if preview_path:
        pathlib.Path(preview_path).write_bytes(preview_jpeg(canon))
    return result


def _decode_child(src: str, preview_path: str | None, conn) -> None:
    """Child process entry point (top level for spawn)."""
    try:
        conn.send(("ok", decode_file(src, preview_path)))
    except ImageRejected as exc:
        conn.send(("rejected", str(exc)))
    except Exception as exc:  # unexpected decoder failure: still a rejected image
        conn.send(("rejected", f"Image could not be decoded ({type(exc).__name__})"))
    finally:
        conn.close()


def model_input_file(src: str, out_path: str) -> None:
    """V-P3: write the model input JPEG of a stored original (canonical image, long side 1280, quality 85)."""
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # V-L2
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            img = Image.open(src, formats=tuple(FORMAT_EXT))
        except Exception:
            raise ImageRejected("File is not a JPEG, PNG or WebP image")
        with img:
            img.load()
            canon = canonical_image(img)
    pathlib.Path(out_path).write_bytes(model_jpeg(canon))


def _model_input_child(src: str, out_path: str, conn) -> None:
    """Child process entry point for the model input (top level for spawn)."""
    try:
        model_input_file(src, out_path)
        conn.send(("ok", {}))
    except ImageRejected as exc:
        conn.send(("rejected", str(exc)))
    except Exception as exc:
        conn.send(("rejected", f"Image could not be decoded ({type(exc).__name__})"))
    finally:
        conn.close()


def model_input_in_child(src: pathlib.Path, out_path: pathlib.Path) -> bytes:
    """V-P3 and V-L5: make the model input in a spawned child and return the JPEG bytes."""
    decode_in_child(src, out_path, target=_model_input_child)
    return out_path.read_bytes()


_slots = threading.BoundedSemaphore(DECODE_SLOTS)


def free_slots() -> int:
    return _slots._value  # for tests and health only


def decode_in_child(
    src: pathlib.Path,
    preview_path: pathlib.Path | None = None,
    timeout: float = DECODE_TIMEOUT_S,
    target: Callable = _decode_child,
) -> dict:
    """V-L5: decode in a spawned child. At most DECODE_SLOTS at once; a slot frees after the child ended."""
    ctx = multiprocessing.get_context("spawn")
    with _slots:
        recv_conn, send_conn = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=target, args=(str(src), str(preview_path) if preview_path else None, send_conn))
        proc.daemon = True
        proc.start()
        send_conn.close()
        timed_out, msg = False, None
        try:
            if recv_conn.poll(timeout):
                msg = recv_conn.recv()
            else:
                timed_out = True
        except EOFError:
            msg = None  # the child ended without a result (crash)
        finally:
            if proc.is_alive():
                proc.kill()
            proc.join()  # reap the child before the slot frees
            recv_conn.close()
    if timed_out:
        raise ImageRejected("Image took too long to decode")
    if msg is None:
        raise ImageRejected("Image decoder stopped without a result")
    kind, value = msg
    if kind != "ok":
        raise ImageRejected(value)
    return value


# ---------------------------------------------------------------- streamed upload (V-L1)


@dataclass
class ReceivedFile:
    path: pathlib.Path
    sha256_hex: str
    size: int


async def receive_upload(
    chunks: AsyncIterator[bytes],
    content_type: str | None,
    content_length: str | None,
    tmp_dir: pathlib.Path,
) -> ReceivedFile:
    """Read a multipart body as a stream into a temporary file. Stop at 10 MB (HTTP 413).

    Only the part named "file" is kept. The caller deletes the temporary file when it is done.
    """
    from python_multipart import MultipartParser
    from python_multipart.multipart import parse_options_header

    body_limit = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD
    if content_length and content_length.isdigit() and int(content_length) > body_limit:
        raise UploadError(413, "Photo is larger than 10 MB")
    ctype, opts = parse_options_header(content_type or "")
    boundary = opts.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary:
        raise UploadError(400, "Send the photo as multipart/form-data in the field 'file'")

    tmp_path = tmp_dir / f"{uuid.uuid4().hex}.upload"
    hasher = hashlib.sha256()
    state = {"field": b"", "value": b"", "headers": {}, "in_file": False, "size": 0, "files": 0}
    fh = open(tmp_path, "wb")

    def on_header_field(data, start, end):
        state["field"] += data[start:end]

    def on_header_value(data, start, end):
        state["value"] += data[start:end]

    def on_header_end():
        state["headers"][state["field"].lower()] = state["value"]
        state["field"], state["value"] = b"", b""

    def on_headers_finished():
        _, disp = parse_options_header(state["headers"].get(b"content-disposition", b""))
        state["in_file"] = disp.get(b"name") == b"file"
        if state["in_file"]:
            state["files"] += 1
            if state["files"] > 1:
                raise UploadError(400, "Send exactly one file")

    def on_part_data(data, start, end):
        if not state["in_file"]:
            return
        piece = data[start:end]
        state["size"] += len(piece)
        if state["size"] > MAX_UPLOAD_BYTES:
            raise UploadError(413, "Photo is larger than 10 MB")
        hasher.update(piece)
        fh.write(piece)

    def on_part_end():
        state["in_file"] = False
        state["headers"] = {}

    parser = MultipartParser(
        boundary,
        {
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
    )
    total = 0
    try:
        async for chunk in chunks:
            total += len(chunk)
            if total > body_limit:
                raise UploadError(413, "Photo is larger than 10 MB")
            parser.write(chunk)
        parser.finalize()
    except UploadError:
        fh.close()
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception:
        fh.close()
        tmp_path.unlink(missing_ok=True)
        raise UploadError(400, "The upload is not valid multipart/form-data")
    fh.close()
    if state["files"] != 1 or state["size"] == 0:
        tmp_path.unlink(missing_ok=True)
        raise UploadError(400, "Send the photo in the field 'file'")
    return ReceivedFile(tmp_path, hasher.hexdigest(), state["size"])


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def remove_quietly(path: pathlib.Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
