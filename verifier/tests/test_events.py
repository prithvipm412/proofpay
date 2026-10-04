"""Event reader (V-E1..V-E5), V-T15, and `finalize` as the event reader uses it (V-J4)."""

import json

import pytest
from eth_abi import encode
from hexbytes import HexBytes
from web3 import Web3

from app.chain import Chain, event_id
from app.events import EventReader
from app.jobs import finalize, job_key
from app.storage import Storage
from tests.conftest import ESCROW, START_BLOCK

H1, H2, H3, H4 = ("0x" + c * 64 for c in "1234")


def dump(storage: Storage) -> dict:
    with storage.read() as c:
        return {
            "claims": [tuple(r) for r in c.execute("SELECT * FROM claims ORDER BY task_id, attempt, role")],
            "jobs": [tuple(r) for r in c.execute("SELECT * FROM jobs ORDER BY job_key")],
            "meta": sorted(tuple(r) for r in c.execute("SELECT * FROM meta")),
        }


def claims(storage):
    with storage.read() as c:
        return {
            (r["task_id"], r["attempt"], r["role"]): (r["sha256"], r["state"])
            for r in c.execute("SELECT * FROM claims")
        }


def job(storage, key):
    with storage.read() as c:
        return c.execute("SELECT * FROM jobs WHERE job_key = ?", (key,)).fetchone()


@pytest.fixture
def reader(settings, storage, chain):
    r = EventReader(settings, storage, chain)
    r.startup()
    return r


def scan_all(reader):
    while reader.scan_once():
        pass


# ---------------------------------------------------------------- V-E3


def test_VE3_events_make_claims_and_jobs(reader, chain, storage, settings):
    chain.create_task(1, H1)
    chain.submit_proof(1, 1, H2)
    scan_all(reader)
    assert claims(storage) == {(1, 0, "before"): (H1, "history"), (1, 1, "after"): (H2, "pending")}
    key = job_key(settings.deployment_id, 1, 1, H2)
    assert key == f"{settings.deployment_id}:1:1:{H2}"
    j = job(storage, key)
    assert (j["state"], j["task_id"], j["attempt"], j["proof_hash"]) == ("queued", 1, 1, H2)
    assert j["event_id"] == event_id(chain.events_list[1].block_number, 0, 0)


def test_VE3_pass_verdict_accepts_fail_verdict_rejects(reader, chain, storage, settings):
    chain.create_task(1, H1)
    chain.submit_proof(1, 1, H2)
    v1 = chain.verdict(1, 1, H2, passed=False, score=20)
    chain.submit_proof(1, 2, H3)
    v2 = chain.verdict(1, 2, H3, passed=True, score=90)
    scan_all(reader)
    c = claims(storage)
    assert c[(1, 1, "after")] == (H2, "rejected")
    assert c[(1, 2, "after")] == (H3, "accepted")
    j1 = job(storage, job_key(settings.deployment_id, 1, 1, H2))
    j2 = job(storage, job_key(settings.deployment_id, 1, 2, H3))
    assert j1["state"] == j2["state"] == "confirmed"
    assert json.loads(j1["tx_hashes"]) == [v1.tx_hash]
    assert json.loads(j2["tx_hashes"]) == [v2.tx_hash]


def test_history_complete_when_caught_up(reader, chain, storage):
    assert storage.meta()["historyStatus"] == "rebuilding"
    scan_all(reader)
    meta = storage.meta()
    assert meta["historyStatus"] == "complete"
    assert int(meta["checkpointBlock"]) == chain.block("finalized").number
    assert meta["checkpointHash"] == chain.block("finalized").hash
    assert reader.caught_up()


def test_VE1_scan_stops_at_safe_head(reader, chain, storage):
    chain.finalized_lag = 5
    chain.create_task(1, H1)  # in the newest block: not final yet
    scan_all(reader)
    assert claims(storage) == {}
    assert int(storage.meta()["checkpointBlock"]) == chain.block("finalized").number
    chain.mine(5)
    scan_all(reader)
    assert (1, 0, "before") in claims(storage)


def test_VE2_batches_are_at_most_100_blocks_and_contiguous(reader, chain):
    chain.mine(350)
    scan_all(reader)
    calls = chain.calls_events
    assert calls[0][0] == START_BLOCK
    assert all(end - start + 1 <= 100 for start, end in calls)
    assert all(calls[i + 1][0] == calls[i][1] + 1 for i in range(len(calls) - 1))
    assert calls[-1][1] == chain.block("finalized").number


# ---------------------------------------------------------------- V-T15


def test_VT15_events_processed_two_times_give_same_database(reader, chain, storage):
    chain.create_task(1, H1)
    chain.submit_proof(1, 1, H2)
    chain.verdict(1, 1, H2, passed=True)
    chain.create_task(2, H2)  # poisoning-like reuse as a before photo
    chain.submit_proof(2, 1, H3)
    chain.verdict(1, 1, H2, passed=True)  # duplicate-looking event at a new event_id
    scan_all(reader)
    first = dump(storage)
    # Process the same history again over the existing rows (no clear).
    with storage.tx() as c:
        Storage.set_meta(c, checkpointBlock=str(START_BLOCK - 1))
    scan_all(reader)
    assert dump(storage) == first
    # And apply every event a third time directly.
    with storage.tx() as c:
        for ev in chain.events_list:
            reader.apply_event(c, ev)
    assert dump(storage) == first


def test_VT15_batch_and_checkpoint_saved_together(reader, chain, storage, monkeypatch):
    chain.create_task(1, H1)
    chain.submit_proof(1, 1, H2)
    scan_all(reader)
    cp_before = storage.meta()["checkpointBlock"]
    snapshot = dump(storage)
    chain.create_task(2, H3)
    chain.submit_proof(2, 1, H4)
    real_apply = reader.apply_event
    calls = {"n": 0}

    def crash_on_second(c, ev):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("crash inside the batch")
        real_apply(c, ev)

    monkeypatch.setattr(reader, "apply_event", crash_on_second)
    with pytest.raises(RuntimeError):
        reader.scan_once()
    assert storage.meta()["checkpointBlock"] == cp_before  # checkpoint not moved
    assert dump(storage) == snapshot  # first event of the batch not saved either
    monkeypatch.setattr(reader, "apply_event", real_apply)
    scan_all(reader)
    assert (2, 1, "after") in claims(storage)


def test_VT15_rpc_failure_mid_scan_keeps_saved_batches(reader, chain, storage):
    chain.mine(250)
    chain.create_task(1, H1)
    fail_block = chain.blocks[-1].number
    chain.fail_events_at = fail_block
    with pytest.raises(ConnectionError):
        reader.scan_once()
    cp = int(storage.meta()["checkpointBlock"])
    assert START_BLOCK - 1 < cp < fail_block
    assert claims(storage) == {}
    chain.fail_events_at = None
    scan_all(reader)
    assert (1, 0, "before") in claims(storage)


def test_VT15_checkpoint_hash_mismatch_starts_rebuild(settings, storage, chain):
    r = EventReader(settings, storage, chain)
    r.startup()
    chain.create_task(1, H1)
    chain.submit_proof(1, 1, H2)
    chain.verdict(1, 1, H2, passed=True)
    scan_all(r)
    assert storage.meta()["historyStatus"] == "complete"
    jobs_before = dump(storage)["jobs"]
    chain.change_history_from(int(storage.meta()["checkpointBlock"]))  # stored hash no longer matches
    r2 = EventReader(settings, storage, chain)
    r2.startup()
    assert storage.meta()["historyStatus"] == "rebuilding"
    assert claims(storage) == {}  # cleared
    assert storage.meta()["checkpointBlock"] == str(START_BLOCK - 1)
    assert dump(storage)["jobs"] == jobs_before  # jobs rows kept
    scan_all(r2)
    assert storage.meta()["historyStatus"] == "complete"
    assert claims(storage)[(1, 1, "after")] == (H2, "accepted")


def test_VE5_no_rebuild_when_checkpoint_matches(settings, storage, chain):
    r = EventReader(settings, storage, chain)
    r.startup()
    chain.create_task(1, H1)
    scan_all(r)
    before = dump(storage)
    EventReader(settings, storage, chain).startup()
    assert dump(storage) == before


def test_VE5_restored_folder_rebuilds(settings, storage, chain):
    r = EventReader(settings, storage, chain)
    chain.create_task(1, H1)
    scan_all(r)
    with storage.tx() as c:
        Storage.set_meta(c, restored="1")
    EventReader(settings, storage, chain).startup()
    meta = storage.meta()
    assert meta["historyStatus"] == "rebuilding" and "restored" not in meta


def test_VE5_incomplete_history_rebuilds(settings, storage, chain):
    r = EventReader(settings, storage, chain)
    chain.create_task(1, H1)
    scan_all(r)
    with storage.tx() as c:
        Storage.set_meta(c, historyStatus="rebuilding")
    EventReader(settings, storage, chain).startup()
    assert claims(storage) == {}


def test_VS5_event_id_text_order_is_chain_order():
    ids = [event_id(9, 2, 5), event_id(10, 0, 0), event_id(10, 0, 11), event_id(10, 1, 0), event_id(100000000, 0, 0)]
    assert sorted(ids) == ids
    assert event_id(68139652, 3, 7) == "000068139652:000003:000007"


# ---------------------------------------------------------------- finalize (V-J4) as used by V-E3


def test_finalize_without_local_job_updates_claim_only(storage, settings):
    with storage.tx() as c:
        finalize(c, settings.deployment_id, 5, 1, H2, True, 77, "0xabc", event_id(1, 0, 0))
    assert claims(storage)[(5, 1, "after")] == (H2, "accepted")
    assert dump(storage)["jobs"] == []


def test_finalize_conflict_when_evaluation_differs(storage, settings):
    key = job_key(settings.deployment_id, 1, 1, H2)
    with storage.tx() as c:
        c.execute(
            "INSERT INTO claims VALUES (1, 1, 'after', ?, 'pending', 'e1')",
            (H2,),
        )
        c.execute(
            "INSERT INTO jobs (job_key, event_id, task_id, attempt, proof_hash, state, evaluation_json,"
            " created_at, updated_at) VALUES (?, 'e1', 1, 1, ?, 'broadcast', ?, 0, 0)",
            (key, H2, json.dumps({"pass": True, "score": 86})),
        )
        finalize(c, settings.deployment_id, 1, 1, H2, True, 50, "0xdef", "e2")
    j = job(storage, key)
    assert (j["state"], j["conflict"]) == ("confirmed", 1)
    assert claims(storage)[(1, 1, "after")] == (H2, "accepted")


def test_finalize_never_changes_claim_of_other_photo(storage, settings):
    with storage.tx() as c:
        c.execute("INSERT INTO claims VALUES (1, 1, 'after', ?, 'pending', 'e1')", (H2,))
        finalize(c, settings.deployment_id, 1, 1, H3, True, 80, "0x1", "e2")
    assert claims(storage)[(1, 1, "after")] == (H2, "pending")


# ---------------------------------------------------------------- real ABI decoding


def test_chain_decodes_real_abi_logs(monkeypatch):
    chain = Chain("http://127.0.0.1:8545", ESCROW)

    def topic(sig):
        return HexBytes(Web3.keccak(text=sig))

    def word(n):
        return HexBytes(n.to_bytes(32, "big"))

    poster = "0x" + "aa" * 20
    logs = [
        {  # VerdictRecorded, later log index in the same block: must sort after ProofSubmitted
            "address": ESCROW,
            "topics": [topic("VerdictRecorded(uint256,uint8,bytes32,bool,uint8,string)"), word(7)],
            "data": HexBytes(encode(["uint8", "bytes32", "bool", "uint8", "string"], [1, bytes.fromhex("22" * 32), True, 86, "AI check: ok"])),
            "blockNumber": 50, "transactionIndex": 1, "logIndex": 4,
            "transactionHash": HexBytes("0x" + "ef" * 32), "blockHash": HexBytes("0x" + "01" * 32), "removed": False,
        },
        {
            "address": ESCROW,
            "topics": [topic("TaskCreated(uint256,address,uint256,bytes32,uint64,uint64)"), word(7), HexBytes(bytes(12) + bytes.fromhex("aa" * 20))],
            "data": HexBytes(encode(["uint256", "bytes32", "uint64", "uint64"], [5 * 10**16, bytes.fromhex("11" * 32), 1000, 1300])),
            "blockNumber": 49, "transactionIndex": 0, "logIndex": 0,
            "transactionHash": HexBytes("0x" + "cd" * 32), "blockHash": HexBytes("0x" + "02" * 32), "removed": False,
        },
        {
            "address": ESCROW,
            "topics": [topic("TaskAccepted(uint256,address)"), word(7), HexBytes(bytes(12) + bytes.fromhex("bb" * 20))],
            "data": HexBytes(b""),
            "blockNumber": 49, "transactionIndex": 1, "logIndex": 0,
            "transactionHash": HexBytes("0x" + "ce" * 32), "blockHash": HexBytes("0x" + "02" * 32), "removed": False,
        },
        {
            "address": ESCROW,
            "topics": [topic("ProofSubmitted(uint256,uint8,bytes32)"), word(7)],
            "data": HexBytes(encode(["uint8", "bytes32"], [1, bytes.fromhex("22" * 32)])),
            "blockNumber": 50, "transactionIndex": 0, "logIndex": 2,
            "transactionHash": HexBytes("0x" + "ab" * 32), "blockHash": HexBytes("0x" + "01" * 32), "removed": False,
        },
    ]
    seen = {}

    def fake_get_logs(params):
        seen.update(params)
        return logs

    monkeypatch.setattr(chain.w3.eth, "get_logs", fake_get_logs)
    events = chain.events(49, 50)
    assert len(seen["topics"][0]) == 4 and seen["address"] == Web3.to_checksum_address(ESCROW)
    assert [e.name for e in events] == ["TaskCreated", "TaskAccepted", "ProofSubmitted", "VerdictRecorded"]
    created, accepted, proof, verdict = events
    assert accepted.args == {"id": 7, "worker": Web3.to_checksum_address("0x" + "bb" * 20)}
    assert created.args == {
        "id": 7, "poster": Web3.to_checksum_address(poster), "amount": 5 * 10**16,
        "beforeHash": "0x" + "11" * 32, "submitBy": 1000, "reviewBy": 1300,
    }
    assert proof.args == {"id": 7, "attempt": 1, "proofHash": "0x" + "22" * 32}
    assert verdict.args["pass"] is True and verdict.args["score"] == 86 and verdict.args["reason"] == "AI check: ok"
    assert verdict.tx_hash == "0x" + "ef" * 32
    assert verdict.event_id == "000000000050:000001:000004"


def test_run_loop_catches_up_without_pause_and_stops(reader, chain, storage):
    import threading
    import time

    chain.mine(6000)  # 60 batches: more than one scan (MAX_BATCHES_PER_SCAN = 50)
    chain.create_task(1, H1)
    t = threading.Thread(target=reader.run, daemon=True)
    started = time.monotonic()
    t.start()
    while storage.meta()["historyStatus"] != "complete" and time.monotonic() - started < 10:
        time.sleep(0.05)
    elapsed = time.monotonic() - started
    reader.stop()
    t.join(timeout=5)
    assert not t.is_alive()
    assert storage.meta()["historyStatus"] == "complete"
    assert elapsed < 2.5  # no 3 s pause between the two scans
    assert (1, 0, "before") in claims(storage)
    assert reader.heartbeat is not None
