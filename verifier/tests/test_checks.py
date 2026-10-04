"""Checks V-C1..V-C6: V-T1, V-T2, V-T3, V-T4 and the soft checks."""

import dataclasses
import shutil

import pytest

from app.checks import REASON_C1, REASON_C3_INCOMPLETE, run_checks
from tests.conftest import DISTANCES, FIXTURES, inproc_decoder, store_file

T_BEFORE_PHOTOS = 1_700_000_000  # a createdAt long before the fixture capture times (2026-10-05)
T_AFTER_PHOTOS = 1_900_000_000  # a createdAt after them


def add_claim(storage, task_id, attempt, role, sha, state):
    with storage.tx() as c:
        c.execute(
            "INSERT INTO claims (task_id, attempt, role, sha256, state, event_id) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, attempt, role, sha, state, f"{task_id:012d}:{attempt:06d}:000000"),
        )


def check(storage, settings, task_id, before, after, created_at=T_BEFORE_PHOTOS):
    return run_checks(storage, settings, task_id, before, after, created_at, decode=inproc_decoder)


def results(run):
    return {c.id: c.result for c in run.checks}


def with_near(settings, hard, warn=10):
    return dataclasses.replace(settings, near_hard=hard, near_warn=warn)


@pytest.fixture
def photos(storage):
    """Store every fixture photo. Returns name -> hash."""
    names = ["a_before.jpg", "a_after.jpg", "b_before.jpg", "b_after.jpg", "a_after_half_q60.jpg"]
    return {n.split(".")[0]: store_file(storage, FIXTURES / n) for n in names}


def test_genuine_pair_passes_all_hard_checks(storage, settings, photos):
    run = check(storage, settings, 1, photos["a_before"], photos["a_after"])
    assert results(run) == {"V-C1": "pass", "V-C2": "pass", "V-C3": "pass", "V-C4": "pass", "V-C5": "pass", "V-C6": "pass"}
    assert run.fail_reason is None and run.vision_allowed
    assert run.get("V-C3").detail == "no accepted proofs to compare"
    assert run.get("V-C5").detail == f"distance {DISTANCES['a_before__a_after']}"


# ---------------------------------------------------------------- V-T1 integrity


def assert_c1_fail(run):
    assert run.get("V-C1").result == "fail"
    assert run.fail_reason == REASON_C1
    assert not run.vision_allowed
    assert all(c.result == "skipped" for c in run.checks if c.id != "V-C1")
    assert [c.id for c in run.checks] == ["V-C1", "V-C2", "V-C3", "V-C4", "V-C5", "V-C6"]


def test_VT1_wrong_before_file(storage, settings, photos):
    shutil.copyfile(FIXTURES / "b_before.jpg", storage.original_path(photos["a_before"]))  # bytes differ
    run = check(storage, settings, 1, photos["a_before"], photos["a_after"])
    assert_c1_fail(run)
    assert "Before" in run.get("V-C1").detail


def test_VT1_wrong_after_file(storage, settings, photos):
    shutil.copyfile(FIXTURES / "b_after.jpg", storage.original_path(photos["a_after"]))
    run = check(storage, settings, 1, photos["a_before"], photos["a_after"])
    assert_c1_fail(run)
    assert "After" in run.get("V-C1").detail


@pytest.mark.parametrize("which", ["before", "after"])
def test_VT1_missing_file(storage, settings, photos, which):
    missing = "0x" + "77" * 32
    before, after = (missing, photos["a_after"]) if which == "before" else (photos["a_before"], missing)
    assert_c1_fail(check(storage, settings, 1, before, after))


def test_VT1_file_that_does_not_decode(storage, settings, photos):
    import hashlib

    garbage = b"not an image" * 50
    sha = "0x" + hashlib.sha256(garbage).hexdigest()
    (storage.originals / f"{sha[2:]}.jpg").write_bytes(garbage)  # hash matches, decode fails
    assert_c1_fail(check(storage, settings, 1, photos["a_before"], sha))


# ---------------------------------------------------------------- V-T2 poisoning


def test_VT2_poisoning_before_photo_does_not_block_honest_proof(storage, settings, photos):
    """Task 2 uses worker A's after photo as its BEFORE photo. A still passes V-C2 and V-C3."""
    add_claim(storage, 1, 0, "before", photos["a_before"], "history")
    add_claim(storage, 1, 1, "after", photos["a_after"], "pending")
    add_claim(storage, 2, 0, "before", photos["a_after"], "history")  # the poisoning claim
    run = check(storage, settings, 1, photos["a_before"], photos["a_after"])
    assert run.get("V-C2").result == "pass"
    assert run.get("V-C3").result == "pass"
    assert run.vision_allowed and run.fail_reason is None
    assert run.get("V-C4").result == "warn"
    assert "before photo of task #2" in run.get("V-C4").detail


def test_VT2_poisoning_with_near_copy_also_only_warns(storage, settings, photos):
    add_claim(storage, 2, 0, "before", photos["a_after_half_q60"], "history")
    run = check(storage, with_near(settings, DISTANCES["a_after__a_after_half_q60"]), 1, photos["a_before"], photos["a_after"])
    assert run.vision_allowed
    assert run.get("V-C4").result == "warn"


# ---------------------------------------------------------------- V-T3 rejected claims


def test_VT3_rejected_claim_does_not_block(storage, settings, photos):
    add_claim(storage, 1, 1, "after", photos["a_after"], "rejected")
    run = check(storage, settings, 2, photos["b_before"], photos["a_after"])
    assert run.get("V-C2").result == "pass"
    assert run.get("V-C3").result == "pass"
    assert run.vision_allowed
    assert run.get("V-C4").result == "warn"
    assert "rejected proof of task #1" in run.get("V-C4").detail


def test_VC8_pending_claim_does_not_block(storage, settings, photos):
    add_claim(storage, 1, 1, "after", photos["a_after"], "pending")
    run = check(storage, settings, 2, photos["b_before"], photos["a_after"])
    assert run.vision_allowed and run.get("V-C4").result == "warn"


# ---------------------------------------------------------------- V-T4 exact and near reuse


def test_VT4_exact_reuse_of_accepted_claim_fails(storage, settings, photos):
    add_claim(storage, 1, 1, "after", photos["a_after"], "accepted")
    run = check(storage, settings, 2, photos["b_before"], photos["a_after"])
    assert run.get("V-C2").result == "fail"
    assert run.fail_reason == "Same photo as the accepted proof of task #1"
    assert run.get("V-C3").result == "skipped"  # remaining hard checks skipped
    assert not run.vision_allowed
    assert {run.get(i).result for i in ("V-C4", "V-C5", "V-C6")} <= {"pass", "warn"}  # soft checks still run


def test_VT4_exact_reuse_same_task_is_not_reuse(storage, settings, photos):
    """An accepted claim of the SAME task does not count (V-C2: a different task)."""
    add_claim(storage, 1, 1, "after", photos["a_after"], "accepted")
    run = check(storage, settings, 1, photos["a_before"], photos["a_after"])
    assert run.get("V-C2").result == "pass"


def test_VT4_near_reuse_inside_threshold_fails(storage, settings, photos):
    d = DISTANCES["a_after__a_after_half_q60"]  # measured, not invented
    add_claim(storage, 1, 1, "after", photos["a_after"], "accepted")
    run = check(storage, with_near(settings, hard=d, warn=max(d, 10)), 2, photos["a_before"], photos["a_after_half_q60"])
    assert run.get("V-C2").result == "pass"
    assert run.get("V-C3").result == "fail"
    assert run.get("V-C3").detail == f"distance {d} to the accepted proof of task #1"
    assert run.fail_reason == "Nearly the same photo as the accepted proof of task #1"
    assert not run.vision_allowed


def test_VT4_near_reuse_with_hard_level_off_only_warns(storage, settings, photos):
    d = DISTANCES["a_after__a_after_half_q60"]
    add_claim(storage, 1, 1, "after", photos["a_after"], "accepted")
    run = check(storage, with_near(settings, hard=-1, warn=max(d, 10)), 2, photos["a_before"], photos["a_after_half_q60"])
    assert run.get("V-C3").result == "warn"
    assert run.vision_allowed and run.fail_reason is None


def test_VT4_near_reuse_just_outside_hard_threshold_warns(storage, settings, photos):
    d = DISTANCES["a_after__a_after_half_q60"]
    assert d >= 1, "this test needs a measured distance of 1 or more"
    add_claim(storage, 1, 1, "after", photos["a_after"], "accepted")
    run = check(storage, with_near(settings, hard=d - 1, warn=10), 2, photos["a_before"], photos["a_after_half_q60"])
    assert run.get("V-C3").result == "warn"


def test_VC3_distant_photo_passes_with_closest_distance(storage, settings, photos):
    add_claim(storage, 1, 1, "after", photos["a_after"], "accepted")
    run = check(storage, settings, 2, photos["b_before"], photos["b_after"])
    assert run.get("V-C3").result == "pass"
    assert run.get("V-C3").detail == f"closest distance {DISTANCES['a_after__b_after']}"


def test_VC3_fails_closed_when_accepted_file_and_cache_are_missing(storage, settings, photos):
    add_claim(storage, 1, 1, "after", photos["a_after"], "accepted")
    storage.original_path(photos["a_after"]).unlink()
    with storage.tx() as c:
        c.execute("UPDATE images SET phash = NULL WHERE sha256 = ?", (photos["a_after"],))
    run = check(storage, settings, 2, photos["b_before"], photos["b_after"])
    assert run.get("V-C3").result == "fail"
    assert run.fail_reason == REASON_C3_INCOMPLETE
    # With the hard level off, the same situation only warns.
    run = check(storage, with_near(settings, -1), 2, photos["b_before"], photos["b_after"])
    assert run.get("V-C3").result == "warn" and run.vision_allowed


def test_VS10_cached_phash_used_when_original_missing(storage, settings, photos):
    add_claim(storage, 1, 1, "after", photos["a_after"], "accepted")
    storage.original_path(photos["a_after"]).unlink()  # cache keyed by SHA-256 still has the pHash
    run = check(storage, settings, 2, photos["b_before"], photos["b_after"])
    assert run.get("V-C3").result == "pass"


# ---------------------------------------------------------------- soft checks


def test_VC5_after_like_before_warns(storage, settings, photos):
    run = check(storage, settings, 1, photos["a_after"], photos["a_after_half_q60"])
    assert run.get("V-C5").result == "warn"
    assert run.vision_allowed  # soft only


def test_VC6_capture_time_cases(storage, settings, photos):
    # a_after: timezone_specified, 2026-10-05T04:30:00Z
    assert check(storage, settings, 1, photos["a_before"], photos["a_after"], T_BEFORE_PHOTOS).get("V-C6").result == "pass"
    late = check(storage, settings, 1, photos["a_before"], photos["a_after"], T_AFTER_PHOTOS).get("V-C6")
    assert (late.result, late.detail) == ("warn", "Capture time is before the task was created")
    no_tz = check(storage, settings, 1, photos["b_before"], photos["b_after"]).get("V-C6")
    assert (no_tz.result, no_tz.detail) == ("warn", "Capture time has no time zone")
    none = check(storage, settings, 1, photos["b_before"], photos["a_before"]).get("V-C6")
    assert (none.result, none.detail) == ("warn", "No capture time")


def test_VC9_reports_never_say_fraud(storage, settings, photos):
    add_claim(storage, 1, 1, "after", photos["a_after"], "accepted")
    add_claim(storage, 3, 0, "before", photos["b_after"], "history")
    for after in (photos["a_after"], photos["a_after_half_q60"], photos["b_after"]):
        run = check(storage, with_near(settings, 4), 2, photos["b_before"], after)
        text = " ".join([run.fail_reason or ""] + [c.detail for c in run.checks]).lower()
        assert "fraud" not in text


def test_reasons_are_ascii_and_short(storage, settings, photos):
    add_claim(storage, 123456, 1, "after", photos["a_after"], "accepted")
    run = check(storage, settings, 2, photos["b_before"], photos["a_after"])
    assert run.fail_reason.isascii() and len(run.fail_reason.encode()) <= 120

