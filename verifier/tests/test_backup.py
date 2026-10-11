"""Backup and restore (V-B1, V-B2) and V-T16."""

import dataclasses
import json
import sqlite3
import tarfile

import pytest

from app.backup import BackupError, chain_pass_verdicts, make_backup, restore_report, unpack
from app.checks import run_checks
from app.events import EventReader
from app.storage import Storage
from tests.conftest import FIXTURES, inproc_decoder, store_file


def scan_all(reader):
    while reader.scan_once():
        pass


def passed_task(chain, task_id, before, after):
    """Create, accept, submit and pass a task on the fake chain; set the contract state of a pass."""
    chain.create_task(task_id, before)
    chain.accept_task(task_id)
    chain.submit_proof(task_id, 1, after)
    chain.verdict(task_id, 1, after, True)
    chain.tasks[task_id].update(status="Approved", disputeUntil=chain.blocks[-1].timestamp + 60)


def chain_pass(chain):
    return chain_pass_verdicts(len(chain.tasks), chain.get_task)


@pytest.fixture
def live(settings, storage, chain):
    """A live data folder with task 1 accepted (photo a_after) and its files stored."""
    photos = {n: store_file(storage, FIXTURES / f"{n}.jpg") for n in ("a_before", "a_after", "b_before", "b_after")}
    reader = EventReader(settings, storage, chain)
    reader.startup()
    passed_task(chain, 1, photos["a_before"], photos["a_after"])
    scan_all(reader)
    return reader, photos


def restored(settings, tmp_path, archive, chain, name="restored"):
    """Unpack to a NEW root, open it like the verifier does, and run the startup rebuild (V-E5)."""
    up = unpack(archive, tmp_path / name)
    assert up.problems == []
    rsettings = dataclasses.replace(settings, data_root=up.root)
    rstorage = Storage(rsettings.data_dir, rsettings.deployment_id, rsettings.max_storage_mb, rsettings.start_block)
    rstorage.open()
    reader = EventReader(rsettings, rstorage, chain)
    reader.startup()
    assert rstorage.meta()["historyStatus"] == "rebuilding"  # restored folder always rebuilds
    scan_all(reader)
    return up, rsettings, rstorage


def test_VB1_backup_has_db_referenced_originals_and_manifest(live, settings, tmp_path):
    result = make_backup(settings.data_dir, tmp_path / "out", settings.config_version, settings.mode)
    with tarfile.open(result.archive) as tar:
        names = tar.getnames()
        manifest = json.load(tar.extractfile("manifest.json"))
    folder = settings.data_dir.name
    assert f"{folder}/proofpay.db" in names
    _, photos = live
    # Only claimed photos are copied: task 1's before and after; b_* are unclaimed uploads.
    assert sorted(manifest["referenced"]) == sorted([photos["a_before"], photos["a_after"]])
    assert result.referenced == 2 and result.copied == 2 and result.missing == []
    originals = [f for f in manifest["files"] if f["kind"] == "original"]
    assert sorted(f["image"] for f in originals) == sorted(manifest["referenced"])
    assert manifest["configVersion"] == settings.config_version
    assert manifest["meta"]["deploymentId"] == settings.deployment_id
    assert manifest["meta"]["historyStatus"] == "complete"


def test_VB1_backup_reports_a_missing_referenced_original(live, settings, storage, tmp_path):
    _, photos = live
    storage.original_path(photos["a_after"]).unlink()
    result = make_backup(settings.data_dir, tmp_path / "out", settings.config_version, settings.mode)
    assert result.missing == [photos["a_after"]] and result.copied == 1


def test_VB1_backup_stops_on_a_corrupt_original(live, settings, storage, tmp_path):
    _, photos = live
    path = storage.original_path(photos["a_after"])
    path.chmod(0o644)
    path.write_bytes(b"not the original")
    with pytest.raises(BackupError):
        make_backup(settings.data_dir, tmp_path / "out", settings.config_version, settings.mode)


def test_VT16_restore_old_backup_then_rebuild(live, settings, storage, chain, tmp_path):
    reader, photos = live
    archive = make_backup(settings.data_dir, tmp_path / "out", settings.config_version, settings.mode).archive

    # After the backup: task 2 gets a pass verdict for photo b_after (its file is not in the backup).
    passed_task(chain, 2, photos["b_before"], photos["b_after"])
    # And task 3 gets a fail verdict (rejected claims stay rejected after the rebuild).
    chain.create_task(3, photos["b_before"])
    chain.accept_task(3)
    chain.submit_proof(3, 1, "0x" + "ee" * 32)
    chain.verdict(3, 1, "0x" + "ee" * 32, False)
    scan_all(reader)

    up, rsettings, rstorage = restored(settings, tmp_path, archive, chain)
    report = restore_report(up, chain_pass(chain))
    assert report.history_status == "complete"
    assert report.accepted == {(1, 1, photos["a_after"]), (2, 1, photos["b_after"])}
    assert report.equal and report.ok
    assert report.claim_counts["accepted"] == 2 and report.claim_counts["rejected"] == 1
    # b_after was uploaded after the backup: listed, expected, not a loss.
    assert set(report.missing_after_backup) == {photos["b_before"], photos["b_after"], "0x" + "ee" * 32}
    assert report.missing_lost == [] and report.missing_at_backup == []

    # A photo accepted after the backup is blocked again on the restored copy. The new worker uploads
    # the photo (POST /upload) before submitting it, so V-C1 passes and V-C2 decides by the hash.
    for name in ("b_before", "b_after"):
        assert store_file(rstorage, FIXTURES / f"{name}.jpg") == photos[name]
    run = run_checks(rstorage, rsettings, 4, photos["a_before"], photos["b_after"], 0, decode=inproc_decoder)
    assert run.get("V-C2").result == "fail" and "task #2" in run.get("V-C2").detail
    # And the photo accepted before the backup too.
    run = run_checks(rstorage, rsettings, 4, photos["b_before"], photos["a_after"], 0, decode=inproc_decoder)
    assert run.get("V-C2").result == "fail" and "task #1" in run.get("V-C2").detail


def test_VT16_comparison_detects_a_difference(live, settings, chain, tmp_path):
    archive = make_backup(settings.data_dir, tmp_path / "out", settings.config_version, settings.mode).archive
    up, _, _ = restored(settings, tmp_path, archive, chain)
    extra = chain_pass(chain) | {(9, 1, "0x" + "99" * 32)}  # a pass verdict the claims do not have
    report = restore_report(up, extra)
    assert not report.equal and not report.ok


def test_VB2_restore_needs_a_new_folder(live, settings, tmp_path):
    archive = make_backup(settings.data_dir, tmp_path / "out", settings.config_version, settings.mode).archive
    target = tmp_path / "used"
    target.mkdir()
    (target / "something").write_text("x")
    with pytest.raises(BackupError):
        unpack(archive, target)


def test_VB2_restore_verifies_every_file(live, settings, tmp_path):
    archive = make_backup(settings.data_dir, tmp_path / "out", settings.config_version, settings.mode).archive
    # Change one original inside a copy of the archive.
    work = tmp_path / "work"
    with tarfile.open(archive) as tar:
        tar.extractall(work, filter="data")
    manifest = json.loads((work / "manifest.json").read_text())
    victim = work / next(f["path"] for f in manifest["files"] if f["kind"] == "original")
    victim.chmod(0o644)
    victim.write_bytes(b"changed")
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        for child in work.iterdir():
            tar.add(child, arcname=child.name)
    up = unpack(bad, tmp_path / "restored-bad")
    assert len(up.problems) == 1 and "hash or size differs" in up.problems[0]
    conn = sqlite3.connect(up.data_dir / "proofpay.db")
    try:  # a failed verification does not mark the folder restored-and-usable
        assert conn.execute("SELECT value FROM meta WHERE key = 'restored'").fetchone() is None
    finally:
        conn.close()


def test_VB2_restore_rejects_paths_outside_the_folder(tmp_path):
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("x")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escaped.txt")
    with pytest.raises(tarfile.TarError):
        unpack(evil, tmp_path / "new")
    assert not (tmp_path / "escaped.txt").exists()


def test_chain_pass_verdicts_use_dispute_until():
    tasks = {
        1: {"disputeUntil": 0, "attempts": 1, "proofHash": "0x" + "11" * 32},  # never passed
        2: {"disputeUntil": 123, "attempts": 2, "proofHash": "0x" + "22" * 32},  # passed on attempt 2
    }
    assert chain_pass_verdicts(2, tasks.__getitem__) == {(2, 2, "0x" + "22" * 32)}
