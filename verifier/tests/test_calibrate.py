"""scripts/calibrate.py: layout, place split, transforms, distance categories and V-C11 counts (M5)."""

import importlib.util
import json
import pathlib
import shutil
import sys

import numpy as np
import pytest
from PIL import Image

from app.images import ImageRejected, canonical_image, decode_file, phash_hex

from tests.conftest import DISTANCES, FIXTURES

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "calibrate.py"
spec = importlib.util.spec_from_file_location("calibrate", SCRIPT)
calibrate = importlib.util.module_from_spec(spec)
sys.modules["calibrate"] = calibrate  # dataclasses look up their module
spec.loader.exec_module(calibrate)


def smooth_image(seed: int, path: pathlib.Path) -> pathlib.Path:
    """A photo-like test image: random 8 x 8 colors, scaled up smoothly."""
    small = np.random.default_rng(seed).integers(0, 256, (8, 8, 3), dtype=np.uint8)
    Image.fromarray(small).resize((320, 240), Image.Resampling.BICUBIC).save(path)
    return path


def make_layout(root: pathlib.Path) -> pathlib.Path:
    """Place 'bench' has two occasions (fixture pair a, and a small change); 'park' has fixture pair b."""
    (root / "bench").mkdir(parents=True)
    (root / "park").mkdir()
    shutil.copy(FIXTURES / "a_before.jpg", root / "bench" / "day1_before.jpg")
    shutil.copy(FIXTURES / "a_after.jpg", root / "bench" / "day1_after.jpg")
    smooth_image(1, root / "bench" / "small-day2_before.png")
    smooth_image(2, root / "bench" / "small-day2_after.webp")
    shutil.copy(FIXTURES / "b_before.jpg", root / "park" / "x_before.jpg")
    shutil.copy(FIXTURES / "b_after.jpg", root / "park" / "x_after.jpg")
    return root


def test_read_layout_pairs_and_small_flag(tmp_path):
    pairs = calibrate.read_layout(make_layout(tmp_path))
    assert [(p.place, p.occasion, p.small) for p in pairs] == [
        ("bench", "day1", False),
        ("bench", "small-day2", True),
        ("park", "x", False),
    ]
    assert pairs[1].after.name == "small-day2_after.webp"


def test_read_layout_reports_every_problem(tmp_path):
    root = make_layout(tmp_path)
    (root / "park" / "x_after.jpg").unlink()  # missing partner
    (root / "park" / "photo.jpg").write_bytes(b"x")  # bad name
    (root / "park" / "y_before.heic").write_bytes(b"x")  # format the verifier does not accept
    shutil.copy(root / "bench" / "day1_before.jpg", root / "bench" / "day1_before.png")  # two before photos
    with pytest.raises(calibrate.LayoutError) as exc:
        calibrate.read_layout(root)
    text = str(exc.value)
    assert "occasion x has no after photo" in text
    assert "park/photo.jpg" in text and "park/y_before.heic" in text
    assert "two before photos for occasion day1" in text


def test_read_layout_needs_pairs(tmp_path):
    with pytest.raises(calibrate.LayoutError):
        calibrate.read_layout(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(calibrate.LayoutError):
        calibrate.read_layout(tmp_path / "empty")


def test_split_is_deterministic_disjoint_and_two_thirds():
    places = [f"place{i:02d}" for i in range(15)]
    cal, val = calibrate.split_places(places)
    assert (cal, val) == calibrate.split_places(list(reversed(places)))
    assert len(cal) == 10 and len(val) == 5
    assert not set(cal) & set(val) and set(cal) | set(val) == set(places)
    assert calibrate.split_places(["a", "b"]) in ((["a"], ["b"]), (["b"], ["a"]))
    assert calibrate.split_places(["only"]) == (["only"], [])


def test_transforms_go_through_the_verifier_decode_path(tmp_path):
    canon = calibrate.canonical_from_file(FIXTURES / "a_after.jpg")
    for name, fn in calibrate.TRANSFORMS.items():
        data, ext = fn(canon)
        path = tmp_path / f"{name}.{ext}"
        path.write_bytes(data)
        info = decode_file(str(path))
        assert len(info["phash"]) == 16
        assert calibrate.transform_hash(canon, name, tmp_path) == info["phash"]
        if name == "resize50":
            assert (info["width"], info["height"]) == (canon.width // 2, canon.height // 2)
        if name == "crop10":
            assert info["width"] == canon.width - 2 * round(canon.width * 0.05)
        if name == "screenshot":
            assert (info["format"], info["width"], info["height"]) == ("PNG", 1080, 2340)
        if name in ("jpeg60", "rotate5"):
            assert (info["width"], info["height"]) == canon.size


def test_measure_categories(tmp_path):
    pairs = calibrate.read_layout(make_layout(tmp_path))
    records, notes = calibrate.measure(pairs)
    by = {c: [r for r in records if r.category == c] for c in ("genuine", "same_place", "other_place", "transform")}
    assert len(by["genuine"]) == 3
    assert len(by["same_place"]) == 4  # 2 photos of day1 x 2 photos of small-day2
    assert len(by["other_place"]) == 8  # 4 bench photos x 2 park photos
    assert len(by["transform"]) == 6 * len(calibrate.TRANSFORMS)
    genuine_a = next(r for r in by["genuine"] if r.a == "bench/day1_before.jpg")
    assert genuine_a.distance == DISTANCES["a_before__a_after"]  # measured fixture value
    assert [r.small for r in by["genuine"]] == [False, True, False]
    assert notes == []


def test_measure_notes_duplicate_files(tmp_path):
    root = make_layout(tmp_path)
    shutil.copy(root / "park" / "x_after.jpg", root / "park" / "x_before.jpg")
    records, notes = calibrate.measure(calibrate.read_layout(root), with_transforms=False)
    assert any("the same file as" in n for n in notes)
    assert not [r for r in records if r.category == "transform"]


def test_mpo_uses_first_frame(tmp_path):
    first = smooth_image(3, tmp_path / "f1.png")
    second = smooth_image(4, tmp_path / "f2.png")
    mpo = tmp_path / "day1_after.jpg"
    with Image.open(first) as a, Image.open(second) as b:
        a.convert("RGB").save(mpo, "MPO", save_all=True, append_images=[b.convert("RGB")])
    with pytest.raises(ImageRejected):
        decode_file(str(mpo))  # the verifier still rejects MPO (V-L3, V-L4)
    phash, note = calibrate.photo_hash(mpo)
    assert note == "MPO: first frame"
    with Image.open(mpo) as img:
        img.seek(0)
        assert phash == phash_hex(canonical_image(img))


def test_unreadable_photo_gives_a_clear_message(tmp_path):
    bad = tmp_path / "day1_before.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(ImageRejected, match="Export the photo as JPEG"):
        calibrate.photo_hash(bad)


def rec(category, d, transform=""):
    return calibrate.Record(category, "p/a", "p/b", d, transform=transform)


def test_evaluate_counts_and_v_c11():
    records = [rec("genuine", 3), rec("same_place", 10), rec("genuine", 20), rec("other_place", 9)]
    records += [rec("transform", d, "jpeg60") for d in (0, 2, 5)] + [rec("transform", d, "screenshot") for d in (12, 30)]
    counts = calibrate.evaluate(records, near_hard=4, near_warn=10)
    assert (counts["honest_pairs"], counts["honest_hard_fail"], counts["honest_warn"]) == (3, 1, 1)
    assert (counts["transform_hard_fail"], counts["transform_warn"], counts["transform_missed"]) == (2, 1, 2)
    assert counts["by_transform"]["jpeg60"] == {"hard_fail": 2, "warn": 1, "missed": 0}
    assert counts["by_transform"]["screenshot"] == {"hard_fail": 0, "warn": 0, "missed": 2}
    assert counts["other_place_warn_or_hard"] == 1
    assert calibrate.v_c11(records, 4).startswith("FAIL") and "NEAR_HARD = -1" in calibrate.v_c11(records, 4)
    assert calibrate.v_c11(records, 2).startswith("PASS")

    off = calibrate.evaluate(records, near_hard=-1, near_warn=10)
    assert off["honest_hard_fail"] == 0 and off["transform_hard_fail"] == 0
    assert off["transform_warn"] == 3 and off["honest_warn"] == 2
    assert "off" in calibrate.v_c11(records, -1)


def test_main_end_to_end(tmp_path, capsys):
    root = make_layout(tmp_path / "photos")
    out = tmp_path / "result.json"
    code = calibrate.main(["--photos", str(root), "--near-hard", "4", "--near-warn", "10", "--json", str(out)])
    assert code == 0
    result = json.loads(out.read_text())
    cal, val = result["split"]["calibration"], result["split"]["validation"]
    assert sorted(cal + val) == ["bench", "park"] and not set(cal) & set(val)
    assert "v_c11" in result["sets"]["validation"] and "counts" in result["sets"]["calibration"]
    printed = capsys.readouterr().out
    assert "WARNING: M5 asks for at least 30 pairs" in printed
    assert "=== calibration set" in printed and "=== validation set" in printed


def test_main_rejects_bad_layout_and_bad_thresholds(tmp_path, capsys):
    assert calibrate.main(["--photos", str(tmp_path / "missing")]) == 2
    with pytest.raises(SystemExit):
        calibrate.main(["--photos", str(tmp_path), "--near-hard", "11", "--near-warn", "10"])
    with pytest.raises(SystemExit):
        calibrate.main(["--photos", str(tmp_path), "--near-hard", "4"])
