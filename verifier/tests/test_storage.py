"""Storage (V-S1..V-S10, V-R5) and the storage parts of V-T21."""

import os
import shutil
import sqlite3
import threading
import time

import pytest

from app.images import MAX_UPLOAD_BYTES, ReceivedFile, UploadError, sha256_file
from app.storage import STORAGE_RESERVE_BYTES, DataFolderError, Storage, norm_hash
from tests.conftest import FIXTURES, fixture_hash, store_file

DAY = 24 * 3600


def test_VS4_tables_and_wal(storage):
    with storage.read() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"meta", "images", "claims", "jobs", "counters"} <= tables
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    meta = storage.meta()
    assert meta["deploymentId"] == storage.deployment_id
    assert meta["historyStatus"] == "rebuilding"
    assert meta["checkpointBlock"] == str(storage.start_block - 1)


def test_VR5_other_deployment_stops(settings, storage):
    other = Storage(settings.data_dir, "31337:0x" + "ab" * 20, 2000, 100)
    with pytest.raises(DataFolderError):
        other.open()


def test_VR5_reopen_same_deployment_ok(settings, storage):
    Storage(settings.data_dir, settings.deployment_id, 2000, 100).open()


def test_VS1_VS2_store_original_and_preview(storage):
    sha = store_file(storage, FIXTURES / "a_after.jpg")
    assert sha == fixture_hash("a_after.jpg")
    original = storage.original_path(sha)
    assert original.name == f"{sha[2:]}.jpg"
    assert "0x" + sha256_file(original) == sha
    assert storage.preview_path(sha).exists()
    row = storage.image(sha)
    assert (row["format"], row["width"], row["height"]) == ("JPEG", 320, 240)
    assert row["exif_time_status"] == "timezone_specified"
    assert len(row["phash"]) == 16
    assert list(storage.tmp.iterdir()) == []  # temporary files removed


def test_VS1_png_named_jpg_stored_as_png(storage, tmp_path):
    src = tmp_path / "x.jpg"
    shutil.copyfile(FIXTURES / "transparent.png", src)
    sha = store_file(storage, src)
    assert storage.original_path(sha).suffix == ".png"


def test_V09_existing_hash_returned_without_decode(storage):
    sha = store_file(storage, FIXTURES / "a_after.jpg")

    def must_not_decode(*_):
        raise AssertionError("decoded again")

    assert store_file(storage, FIXTURES / "a_after.jpg", decoder=must_not_decode) == sha


def test_VS1_never_overwrite(storage):
    sha = store_file(storage, FIXTURES / "a_after.jpg")
    path = storage.original_path(sha)
    before = path.stat().st_ino
    with storage.tx() as c:  # make the next store take the full path
        c.execute("DELETE FROM images")
    store_file(storage, FIXTURES / "a_after.jpg")
    assert path.stat().st_ino == before


def test_norm_hash():
    h = "0x" + "AB" * 32
    assert norm_hash(h) == "0x" + "ab" * 32
    assert norm_hash("ab" * 32) == "0x" + "ab" * 32
    for bad in ("0x1234", "zz" * 32, "", "../../etc/passwd"):
        with pytest.raises(ValueError):
            norm_hash(bad)


# ---------------------------------------------------------------- V-S7 storage admission


def make_storage(settings, max_mb: int) -> Storage:
    s = Storage(settings.data_dir, settings.deployment_id, max_mb, settings.start_block)
    s.open()
    return s


def test_VS7_admission_formula(settings):
    s = make_storage(settings, 400)
    used = s.used_bytes()
    room = s.max_storage_bytes - used - MAX_UPLOAD_BYTES - STORAGE_RESERVE_BYTES
    assert room > 0
    with s.admit_upload():
        pass
    (s.tmp / "filler").write_bytes(b"\0" * (room + 1))
    with pytest.raises(UploadError) as exc:
        with s.admit_upload():
            pass
    assert exc.value.status == 507


def test_VT21_concurrent_uploads_cannot_pass_limit(settings):
    # Room for exactly 3 uploads of 10 MB above the 300 MB reserve.
    s = make_storage(settings, 400)
    used = s.used_bytes()
    filler = s.max_storage_bytes - used - STORAGE_RESERVE_BYTES - 3 * MAX_UPLOAD_BYTES
    (s.tmp / "filler").write_bytes(b"\0" * filler)
    admitted, refused = [], []
    gate = threading.Barrier(8)
    release = threading.Event()

    def try_upload():
        gate.wait()
        try:
            with s.admit_upload():
                admitted.append(1)
                release.wait(5)
        except UploadError as exc:
            refused.append(exc.status)

    threads = [threading.Thread(target=try_upload) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.5)
    release.set()
    for t in threads:
        t.join()
    assert len(admitted) == 3
    assert refused == [507] * 5
    assert s._reserved == 0


def test_VS7_used_bytes_counts_db_and_tmp(storage):
    base = storage.used_bytes()
    (storage.tmp / "partial").write_bytes(b"\0" * 1000)
    assert storage.used_bytes() == base + 1000
    db_files = sum(
        os.path.getsize(str(storage.db_path) + suf)
        for suf in ("", "-wal", "-shm")
        if os.path.exists(str(storage.db_path) + suf)
    )
    assert base >= db_files > 0


# ---------------------------------------------------------------- V-S8 cleanup


def age_upload(storage, sha, seconds):
    with storage.tx() as c:
        c.execute("UPDATE images SET created_at = created_at - ? WHERE sha256 = ?", (seconds, sha))


def complete_history(storage):
    with storage.tx() as c:
        Storage.set_meta(c, historyStatus="complete")


def add_claim(storage, task_id, attempt, role, sha, state, event_id="000000000001:000000:000000"):
    with storage.tx() as c:
        c.execute(
            "INSERT INTO claims (task_id, attempt, role, sha256, state, event_id) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, attempt, role, sha, state, event_id),
        )


def test_VS8_cleanup_deletes_only_old_unreferenced(storage):
    complete_history(storage)
    old_free = store_file(storage, FIXTURES / "a_before.jpg")
    old_ref = store_file(storage, FIXTURES / "a_after.jpg")
    new_free = store_file(storage, FIXTURES / "b_before.jpg")
    age_upload(storage, old_free, DAY + 10)
    age_upload(storage, old_ref, DAY + 10)
    add_claim(storage, 1, 1, "after", old_ref, "pending")
    now = int(time.time())
    deleted = storage.cleanup(now, checkpoint_time=now, caught_up=True)
    assert deleted == [old_free]
    assert storage.original_path(old_free) is None and not storage.preview_path(old_free).exists()
    assert storage.original_path(old_ref) is not None
    assert storage.original_path(new_free) is not None


def test_VT21_cleanup_keeps_hash_uploaded_after_its_event(storage):
    """The claim came from an event BEFORE the upload (the frontend uploaded late). Never delete it."""
    complete_history(storage)
    sha = fixture_hash("a_after.jpg")
    add_claim(storage, 4, 0, "before", sha, "history")  # event first
    store_file(storage, FIXTURES / "a_after.jpg")  # upload later
    age_upload(storage, sha, 10 * DAY)
    now = int(time.time())
    assert storage.cleanup(now + 30 * DAY, checkpoint_time=now + 30 * DAY, caught_up=True) == []
    assert storage.original_path(sha) is not None


@pytest.mark.parametrize("state", ["history", "pending", "accepted", "rejected"])
def test_VT21_cleanup_keeps_every_referenced_state(storage, state):
    complete_history(storage)
    sha = store_file(storage, FIXTURES / "a_after.jpg")
    add_claim(storage, 1, 1, "after", sha, state)
    age_upload(storage, sha, 100 * DAY)
    now = int(time.time())
    assert storage.cleanup(now, now, True) == []


def test_VS8_needs_reader_caught_up_and_checkpoint_24h_after_upload(storage):
    complete_history(storage)
    sha = store_file(storage, FIXTURES / "a_before.jpg")
    age_upload(storage, sha, DAY + 100)
    created = storage.image(sha)["created_at"]
    now = int(time.time())
    assert storage.cleanup(now, now, caught_up=False) == []
    assert storage.cleanup(now, checkpoint_time=created + DAY, caught_up=True) == []  # not MORE than 24 h
    assert storage.cleanup(now, checkpoint_time=None, caught_up=True) == []
    assert storage.cleanup(now, checkpoint_time=created + DAY + 1, caught_up=True) == [sha]


def test_VS8_no_cleanup_during_rebuild(storage):
    sha = store_file(storage, FIXTURES / "a_before.jpg")
    age_upload(storage, sha, 10 * DAY)
    now = int(time.time())
    assert storage.meta()["historyStatus"] == "rebuilding"
    assert storage.cleanup(now, now, True) == []


def test_reupload_refreshes_cleanup_clock(storage):
    complete_history(storage)
    sha = store_file(storage, FIXTURES / "a_before.jpg")
    age_upload(storage, sha, 10 * DAY)
    store_file(storage, FIXTURES / "a_before.jpg")
    now = int(time.time())
    assert storage.cleanup(now, now, True) == []


def test_stale_tmp_files_removed(storage):
    stale = storage.tmp / "old.upload"
    stale.write_bytes(b"x")
    old = time.time() - 2 * 3600
    os.utime(stale, (old, old))
    fresh = storage.tmp / "new.upload"
    fresh.write_bytes(b"x")
    storage.cleanup(int(time.time()), None, False)
    assert not stale.exists() and fresh.exists()


def test_tx_rolls_back_on_error(storage):
    with pytest.raises(RuntimeError):
        with storage.tx() as c:
            Storage.set_meta(c, historyStatus="complete")
            raise RuntimeError("crash")
    assert storage.meta()["historyStatus"] == "rebuilding"


def test_claims_unique_per_task_attempt_role(storage):
    add_claim(storage, 1, 1, "after", "0x" + "11" * 32, "pending")
    with pytest.raises(sqlite3.IntegrityError):
        add_claim(storage, 1, 1, "after", "0x" + "22" * 32, "pending")


def test_received_file_cleanup_on_decode_error(storage):
    tmp = storage.tmp / "bad.upload"
    tmp.write_bytes(b"garbage")

    def failing(*_):
        raise ValueError("nope")

    with pytest.raises(ValueError):
        storage.store_upload(ReceivedFile(tmp, sha256_file(tmp), 7), failing)
    assert list(storage.tmp.iterdir()) == []
