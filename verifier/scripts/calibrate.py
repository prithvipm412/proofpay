"""pHash calibration for the near-reuse thresholds (M5 tasks 2 to 4, docs/calibration.md, V-C3, V-C11).

Usage (from the verifier/ folder):
    .venv/bin/python scripts/calibrate.py [--photos DIR] [--near-hard N --near-warn M] [--json OUT]

Folder layout (default DIR: <repo>/verifier-data/calibration, which git ignores):

    <place>/<occasion>_before.<jpg|jpeg|png|webp>
    <place>/<occasion>_after.<jpg|jpeg|png|webp>

One folder for each place. One occasion is one visit: a before photo and an after photo of one task.
Start the occasion name with "small-" when the change between the two photos is small.
Use two or more occasions in one place to measure same-place repeats.

The script splits the places into a calibration set (about 2/3) and a validation set (about 1/3).
A place is never in both sets. For each set it prints raw pHash distances for:
  genuine        before vs after of the same occasion (honest, different photos)
  same_place     photos of the same place from different occasions (honest, different photos)
  transform      a photo vs a changed copy of itself (reuse attempts): resize 50 %, JPEG quality 60,
                 crop 10 %, screenshot, rotate 5 degrees
  other_place    photos of different places (summary only)
With --near-hard and --near-warn it also prints the counts for those thresholds and applies V-C11
on the validation set. Choose thresholds on the calibration set only.

Each pHash comes from app.images.decode_file, the same code as the verifier (V-P1, V-P2).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
import pathlib
import re
import statistics
import sys
import tempfile
from dataclasses import asdict, dataclass

from PIL import Image, ImageDraw

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.images import ImageRejected, canonical_image, decode_file, distance, phash_hex  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO / "verifier-data" / "calibration"
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
NAME_RE = re.compile(r"^(?P<occasion>[A-Za-z0-9][A-Za-z0-9_-]*)_(?P<role>before|after)$")
SMALL_PREFIX = "small-"
MIN_PAIRS, MIN_PLACES = 30, 10  # M5 task 2
HONEST = ("genuine", "same_place")


class LayoutError(Exception):
    """The photo folder does not follow the layout."""


@dataclass(frozen=True)
class Pair:
    place: str
    occasion: str
    small: bool
    before: pathlib.Path
    after: pathlib.Path


@dataclass
class Record:
    category: str  # genuine, same_place, transform, other_place
    a: str  # "<place>/<file name>"
    b: str  # second photo, or the transform name
    distance: int
    small: bool = False
    transform: str = ""


# ---------------------------------------------------------------- layout and split


def read_layout(root: pathlib.Path) -> list[Pair]:
    """Read the folder layout. Raise LayoutError with every problem found."""
    if not root.is_dir():
        raise LayoutError(f"Folder not found: {root}")
    problems: list[str] = []
    pairs: list[Pair] = []
    for place_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        photos: dict[str, dict[str, pathlib.Path]] = {}
        for f in sorted(place_dir.iterdir()):
            if f.name.startswith(".") or f.is_dir():
                continue
            m = NAME_RE.match(f.stem)
            if f.suffix.lower() not in PHOTO_EXTS or not m:
                problems.append(f"{place_dir.name}/{f.name}: name must be <occasion>_before or _after, .jpg/.png/.webp")
                continue
            roles = photos.setdefault(m["occasion"], {})
            if m["role"] in roles:
                problems.append(f"{place_dir.name}/{f.name}: two {m['role']} photos for occasion {m['occasion']}")
            roles[m["role"]] = f
        for occasion, roles in sorted(photos.items()):
            if set(roles) != {"before", "after"}:
                missing = ({"before", "after"} - set(roles)).pop()
                problems.append(f"{place_dir.name}: occasion {occasion} has no {missing} photo")
                continue
            pairs.append(
                Pair(place_dir.name, occasion, occasion.startswith(SMALL_PREFIX), roles["before"], roles["after"])
            )
    if problems:
        raise LayoutError("\n".join(problems))
    if not pairs:
        raise LayoutError(f"No photo pairs in {root}")
    return pairs


def split_places(places: list[str], seed: str = "") -> tuple[list[str], list[str]]:
    """Deterministic split by place: about 2/3 calibration, 1/3 validation, never overlapping."""
    unique = sorted(set(places))
    ordered = sorted(unique, key=lambda p: hashlib.sha256(f"{seed}{p}".encode()).hexdigest())
    n_cal = round(len(ordered) * 2 / 3)
    if len(ordered) >= 2:
        n_cal = min(max(n_cal, 1), len(ordered) - 1)
    return sorted(ordered[:n_cal]), sorted(ordered[n_cal:])


# ---------------------------------------------------------------- hashing and transforms


def canonical_from_file(path: pathlib.Path) -> Image.Image:
    """Canonical image (V-P1) of the first frame. Used for transforms and for the MPO case."""
    with Image.open(path) as img:
        img.seek(0)
        img.load()
        return canonical_image(img)


def photo_hash(path: pathlib.Path) -> tuple[str, str]:
    """pHash through the verifier decode path. Returns (phash, note).

    The verifier rejects multi-picture JPEG (MPO) from some phone cameras; the frontend converts such
    photos to a single-frame JPEG first (F-13). For MPO only, the pHash is taken from the first frame.
    """
    try:
        return decode_file(str(path))["phash"], ""
    except ImageRejected as exc:
        try:
            with Image.open(path) as img:
                fmt = img.format
        except Exception:
            raise ImageRejected(f"{path.name}: {exc}. Export the photo as JPEG.") from None
        if fmt != "MPO":
            raise ImageRejected(f"{path.name}: {exc}. Export the photo as JPEG.") from None
        return phash_hex(canonical_from_file(path)), "MPO: first frame"


def _jpeg(img: Image.Image, quality: int) -> tuple[bytes, str]:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue(), "jpg"


def t_resize50(img: Image.Image) -> tuple[bytes, str]:
    w, h = img.size
    return _jpeg(img.resize((max(1, w // 2), max(1, h // 2)), Image.Resampling.LANCZOS), 90)


def t_jpeg60(img: Image.Image) -> tuple[bytes, str]:
    return _jpeg(img, 60)


def t_crop10(img: Image.Image) -> tuple[bytes, str]:
    """Keep the central 90 % of the width and of the height (5 % off each edge)."""
    w, h = img.size
    dx, dy = round(w * 0.05), round(h * 0.05)
    return _jpeg(img.crop((dx, dy, w - dx, h - dy)), 90)


def t_rotate5(img: Image.Image) -> tuple[bytes, str]:
    """Rotate 5 degrees counter-clockwise, same size, black corners."""
    return _jpeg(img.rotate(5, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(0, 0, 0)), 90)


def t_screenshot(img: Image.Image) -> tuple[bytes, str]:
    """Simulated phone screenshot of the photo in a gallery app: 1080 x 2340 PNG, black background,
    photo fitted between a 100 px status bar and a 150 px bottom bar."""
    width, height, top, bottom = 1080, 2340, 100, 150
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    area_h = height - top - bottom
    scale = min(width / img.width, area_h / img.height)
    shown = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.Resampling.LANCZOS)
    canvas.paste(shown, ((width - shown.width) // 2, top + (area_h - shown.height) // 2))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, width, top), fill=(32, 32, 32))
    draw.rectangle((0, height - bottom, width, height), fill=(32, 32, 32))
    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue(), "png"


TRANSFORMS = {
    "resize50": t_resize50,
    "jpeg60": t_jpeg60,
    "crop10": t_crop10,
    "screenshot": t_screenshot,
    "rotate5": t_rotate5,
}


def transform_hash(canon: Image.Image, name: str, workdir: pathlib.Path) -> str:
    """pHash of a transformed copy, through the verifier decode path (as an upload would be)."""
    data, ext = TRANSFORMS[name](canon)
    path = workdir / f"t_{name}.{ext}"
    path.write_bytes(data)
    return decode_file(str(path))["phash"]


# ---------------------------------------------------------------- measurement


def measure(pairs: list[Pair], with_transforms: bool = True) -> tuple[list[Record], list[str]]:
    """All distances for one set of pairs. Returns (records, notes)."""
    notes: list[str] = []
    hashes: dict[pathlib.Path, str] = {}
    digests: dict[str, str] = {}
    label = {}
    for pair in pairs:
        for path in (pair.before, pair.after):
            label[path] = f"{pair.place}/{path.name}"
            hashes[path], note = photo_hash(path)
            if note:
                notes.append(f"{label[path]}: {note}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in digests:
                notes.append(f"{label[path]}: the same file as {digests[digest]}")
            digests.setdefault(digest, label[path])

    records: list[Record] = []
    for pair in pairs:
        d = distance(hashes[pair.before], hashes[pair.after])
        records.append(Record("genuine", label[pair.before], label[pair.after], d, pair.small))

    photos = [(pair, path) for pair in pairs for path in (pair.before, pair.after)]
    for (pa, a), (pb, b) in itertools.combinations(photos, 2):
        if pa.place == pb.place and pa.occasion != pb.occasion:
            records.append(Record("same_place", label[a], label[b], distance(hashes[a], hashes[b])))
        elif pa.place != pb.place:
            records.append(Record("other_place", label[a], label[b], distance(hashes[a], hashes[b])))

    if with_transforms:
        with tempfile.TemporaryDirectory() as tmp:
            for _, path in photos:
                canon = canonical_from_file(path)
                for name in TRANSFORMS:
                    d = distance(hashes[path], transform_hash(canon, name, pathlib.Path(tmp)))
                    records.append(Record("transform", label[path], name, d, transform=name))
    return records, notes


def evaluate(records: list[Record], near_hard: int, near_warn: int) -> dict:
    """Raw counts for one set and one pair of thresholds. NEAR_HARD = -1 turns the hard level off."""
    honest = [r for r in records if r.category in HONEST]
    transforms = [r for r in records if r.category == "transform"]
    hard = lambda r: near_hard >= 0 and r.distance <= near_hard  # noqa: E731
    warn = lambda r: not hard(r) and r.distance <= near_warn  # noqa: E731
    return {
        "honest_pairs": len(honest),
        "honest_hard_fail": sum(map(hard, honest)),
        "honest_warn": sum(map(warn, honest)),
        "transform_copies": len(transforms),
        "transform_hard_fail": sum(map(hard, transforms)),
        "transform_warn": sum(map(warn, transforms)),
        "transform_missed": sum(1 for r in transforms if not hard(r) and not warn(r)),
        "by_transform": {
            name: {
                "hard_fail": sum(1 for r in transforms if r.transform == name and hard(r)),
                "warn": sum(1 for r in transforms if r.transform == name and warn(r)),
                "missed": sum(1 for r in transforms if r.transform == name and not hard(r) and not warn(r)),
            }
            for name in TRANSFORMS
        },
        "other_place_warn_or_hard": sum(
            1 for r in records if r.category == "other_place" and (hard(r) or r.distance <= near_warn)
        ),
    }


def v_c11(validation: list[Record], near_hard: int) -> str:
    """V-C11: the hard level stays on only IF no genuine or same-place validation pair is at <= NEAR_HARD."""
    if near_hard < 0:
        return "NEAR_HARD is -1: the hard level of V-C3 is off."
    hits = [r for r in validation if r.category in HONEST and r.distance <= near_hard]
    if hits:
        worst = ", ".join(f"{r.a} ~ {r.b} = {r.distance}" for r in hits[:5])
        return f"FAIL: {len(hits)} honest validation pair(s) at <= {near_hard} ({worst}). Propose NEAR_HARD = -1."
    return f"PASS: no genuine or same-place validation pair at <= {near_hard}. The hard level can stay on."


# ---------------------------------------------------------------- report


def summary(values: list[int]) -> str:
    if not values:
        return "none"
    v = sorted(values)
    p5 = v[max(0, round(0.05 * (len(v) - 1)))]
    return f"n={len(v)} min={v[0]} p5={p5} median={statistics.median(v):g} max={v[-1]}"


def print_set(name: str, places: list[str], pairs: list[Pair], records: list[Record]) -> None:
    print(f"\n=== {name} set: {len(places)} places, {len(pairs)} pairs ===")
    print("places:", ", ".join(places))
    for category in ("genuine", "same_place"):
        rows = sorted((r for r in records if r.category == category), key=lambda r: r.distance)
        print(f"\n{category}: {summary([r.distance for r in rows])}")
        for r in rows:
            print(f"  {r.distance:2d}  {r.a} ~ {r.b}{'  (small change)' if r.small else ''}")
    print("\ntransform (photo vs changed copy):")
    for t in TRANSFORMS:
        values = [r.distance for r in records if r.transform == t]
        print(f"  {t:10s} {summary(values)}  raw={sorted(values)}")
    print(f"\nother_place: {summary([r.distance for r in records if r.category == 'other_place'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pHash calibration for NEAR_HARD and NEAR_WARN (M5).")
    parser.add_argument("--photos", type=pathlib.Path, default=DEFAULT_DIR)
    parser.add_argument("--seed", default="", help="change the place split (default: no seed)")
    parser.add_argument("--near-hard", type=int)
    parser.add_argument("--near-warn", type=int)
    parser.add_argument("--json", type=pathlib.Path, help="write all distances to this file")
    args = parser.parse_args(argv)
    if (args.near_hard is None) != (args.near_warn is None):
        parser.error("give --near-hard and --near-warn together")
    if args.near_hard is not None and not -1 <= args.near_hard <= args.near_warn <= 64:
        parser.error("need -1 <= NEAR_HARD <= NEAR_WARN <= 64 (V-CFG4)")

    try:
        pairs = read_layout(args.photos)
    except LayoutError as exc:
        print(f"Layout problem:\n{exc}", file=sys.stderr)
        return 2

    places = sorted({p.place for p in pairs})
    repeat_places = sorted(p for p in places if sum(q.place == p for q in pairs) >= 2)
    print(f"Photos: {args.photos}")
    print(
        f"{len(pairs)} pairs, {len(places)} places, {sum(p.small for p in pairs)} small-change pairs, "
        f"{len(repeat_places)} places with two or more occasions"
    )
    if len(pairs) < MIN_PAIRS or len(places) < MIN_PLACES:
        print(f"WARNING: M5 asks for at least {MIN_PAIRS} pairs from at least {MIN_PLACES} places.")
    if not any(p.small for p in pairs) or not repeat_places:
        print("WARNING: M5 asks for small-change pairs and for same-place pairs on different occasions.")

    cal_places, val_places = split_places(places, args.seed)
    result: dict = {"split": {"calibration": cal_places, "validation": val_places}, "sets": {}}
    try:
        for name, set_places in (("calibration", cal_places), ("validation", val_places)):
            set_pairs = [p for p in pairs if p.place in set_places]
            records, notes = measure(set_pairs)
            print_set(name, set_places, set_pairs, records)
            for note in notes:
                print(f"NOTE {note}")
            entry = {"notes": notes, "records": [asdict(r) for r in records]}
            if args.near_hard is not None:
                entry["counts"] = evaluate(records, args.near_hard, args.near_warn)
                print(f"\ncounts at NEAR_HARD={args.near_hard}, NEAR_WARN={args.near_warn}:")
                print(json.dumps(entry["counts"], indent=2))
                if name == "validation":
                    entry["v_c11"] = v_c11(records, args.near_hard)
                    print(f"\nV-C11: {entry['v_c11']}")
            result["sets"][name] = entry
    except ImageRejected as exc:
        print(f"Photo problem: {exc}", file=sys.stderr)
        return 2

    if args.json:
        args.json.write_text(json.dumps(result, indent=2))
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
