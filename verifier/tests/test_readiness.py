"""Readiness (V-R1..V-R10), V-T14, startup checks (V-03, V-CFG3, V-R5) and the signer lock (V-07)."""

import os
import subprocess
import sys
import time

import pytest

from app.events import EventReader
from app.main import StartupError, prepare
from app.readiness import Readiness, SignerLockError, acquire_signer_lock
from app.storage import Storage
from tests.conftest import ANVIL_ADDR_1, FakeChain, make_settings


@pytest.fixture
def live(tmp_path):
    return make_settings(tmp_path, VERIFIER_MODE="live")


def ready_setup(settings, chain):
    storage = Storage(settings.data_dir, settings.deployment_id, settings.max_storage_mb, settings.start_block)
    storage.open()
    reader = EventReader(settings, storage, chain)
    reader.startup()
    while reader.scan_once():
        pass
    reader.heartbeat = time.monotonic()
    worker = {"t": time.monotonic()}
    readiness = Readiness(settings, storage, chain, reader, worker_heartbeat=lambda: worker["t"])
    return storage, reader, readiness, worker


def failed(readiness):
    return readiness.refresh()["failed"]


def test_all_items_pass_when_healthy(live):
    chain = FakeChain()
    _, _, readiness, _ = ready_setup(live, chain)
    snap = readiness.refresh()
    assert snap["failed"] == [] and snap["ready"] is True
    assert [c["id"] for c in snap["checks"]] == [f"V-R{i}" for i in range(1, 11)]
    assert snap["historyStatus"] == "complete" and snap["eventLagBlocks"] == 0


def test_VT14_verifier_address_mismatch(live):
    chain = FakeChain(verifier="0x" + "9" * 40)
    chain.balances[ANVIL_ADDR_1] = 10**18
    _, _, readiness, _ = ready_setup(live, chain)
    assert failed(readiness) == ["V-R3"]
    assert readiness.ready is False


def test_VT14_stale_heartbeat(live):
    _, reader, readiness, worker = ready_setup(live, FakeChain())
    reader.heartbeat = time.monotonic() - 31
    assert failed(readiness) == ["V-R9"]
    reader.heartbeat = time.monotonic()
    worker["t"] = time.monotonic() - 31
    assert failed(readiness) == ["V-R9"]


def test_VT14_worker_loop_missing_fails_R9(live):
    chain = FakeChain()
    storage, reader, _, _ = ready_setup(live, chain)
    readiness = Readiness(live, storage, chain, reader)  # no worker loop yet (M4)
    snap = readiness.refresh()
    assert snap["failed"] == ["V-R9"]
    assert "workerLoop not running" in next(c for c in snap["checks"] if c["id"] == "V-R9")["detail"]


def test_VT14_rebuilding(live):
    storage, _, readiness, _ = ready_setup(live, FakeChain())
    with storage.tx() as c:
        Storage.set_meta(c, historyStatus="rebuilding")
    assert failed(readiness) == ["V-R8"]


def test_VT14_blocked_nonce(live):
    storage, _, readiness, _ = ready_setup(live, FakeChain())
    with storage.tx() as c:
        c.execute(
            "INSERT INTO jobs (job_key, event_id, task_id, attempt, proof_hash, state, created_at, updated_at)"
            " VALUES ('k', 'e', 1, 1, '0x', 'blocked_nonce', 0, 0)"
        )
    assert failed(readiness) == ["V-R10"]


def test_VR4_low_balance_warns_and_fails(live):
    chain = FakeChain()
    chain.balances[ANVIL_ADDR_1] = 4 * 10**16  # 0.04 < 0.05 MON
    _, _, readiness, _ = ready_setup(live, chain)
    snap = readiness.refresh()
    assert snap["failed"] == ["V-R4"] and "paused_low_balance" in snap["warnings"]


def test_VR1_wrong_chain(live):
    chain = FakeChain()
    _, _, readiness, _ = ready_setup(live, chain)
    chain.cid = 10143
    assert failed(readiness) == ["V-R1"]


def test_VR2_no_contract(live):
    chain = FakeChain()
    _, _, readiness, _ = ready_setup(live, chain)
    chain.has_code = False
    assert failed(readiness) == ["V-R2"]


def test_VR2_contract_call_error(live, monkeypatch):
    chain = FakeChain()
    _, _, readiness, _ = ready_setup(live, chain)

    def broken():
        raise ValueError("Could not decode contract function call")

    monkeypatch.setattr(chain, "review_grace", broken)
    assert "V-R2" in failed(readiness)


def test_VR7_event_lag(live):
    chain = FakeChain()
    _, _, readiness, _ = ready_setup(live, chain)
    chain.mine(100)  # 40 s of blocks not yet scanned
    assert failed(readiness) == ["V-R7"]


def test_readonly_mode_does_not_need_signer(tmp_path):
    s = make_settings(tmp_path, VERIFIER_MODE="readonly", VERIFIER_PRIVATE_KEY="")
    chain = FakeChain(verifier="0x" + "9" * 40)
    _, _, readiness, _ = ready_setup(s, chain)
    snap = readiness.refresh()
    assert snap["ready"] is True and snap["mode"] == "readonly"


def test_V04_health_has_no_secrets(live):
    _, _, readiness, _ = ready_setup(live, FakeChain())
    text = repr(readiness.refresh())
    assert live.signer_key[2:] not in text and live.vision_api_key not in text


# ---------------------------------------------------------------- startup (prepare)


def test_startup_stops_on_wrong_chain_id(tmp_path):
    with pytest.raises(StartupError, match="chain ID"):
        prepare(make_settings(tmp_path), FakeChain(chain_id=10143))


def test_startup_stops_without_contract_code(tmp_path):
    chain = FakeChain()
    chain.has_code = False
    with pytest.raises(StartupError, match="No contract code"):
        prepare(make_settings(tmp_path), chain)


def test_VCFG3_start_block_after_current_block(tmp_path):
    chain = FakeChain()
    with pytest.raises(StartupError, match="START_BLOCK"):
        prepare(make_settings(tmp_path, START_BLOCK=str(chain.latest_block_number() + 1)), chain)


def test_VT17_second_live_start_on_same_folder_stops(live):
    first = prepare(live, FakeChain())
    try:
        with pytest.raises(SignerLockError):
            prepare(live, FakeChain())
    finally:
        first.close()
    prepare(live, FakeChain()).close()  # free again after the first one stopped


def test_VT17_lock_blocks_another_process(live, tmp_path):
    services = prepare(live, FakeChain())
    code = (
        "import sys, pathlib; sys.path.insert(0, '.');"
        "from app.readiness import acquire_signer_lock, SignerLockError\n"
        f"try:\n    acquire_signer_lock(pathlib.Path({str(live.data_dir)!r}))\n    print('GOT')\n"
        "except SignerLockError:\n    print('LOCKED')"
    )
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30).stdout
        assert out.strip() == "LOCKED"
    finally:
        services.close()
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30).stdout
    assert out.strip() == "GOT"


def test_readonly_takes_no_lock(tmp_path):
    s = make_settings(tmp_path, VERIFIER_MODE="readonly")
    a = prepare(s, FakeChain())
    b = prepare(s, FakeChain())
    assert a.lock_fd is None and b.lock_fd is None
    fd = acquire_signer_lock(s.data_dir)  # a live start is still possible
    os.close(fd)
