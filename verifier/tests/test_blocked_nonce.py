"""Operator recovery for blocked_nonce jobs (scripts/blocked_nonce.py; V-J3 step 3, V-R10, README)."""

import importlib.util
import pathlib

import pytest

from tests.test_jobs import W2, H, fast_timers  # noqa: F401  (fast_timers is an autouse fixture)

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "blocked_nonce.py"
spec = importlib.util.spec_from_file_location("blocked_nonce", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


@pytest.fixture
def h(tmp_path):
    return H(tmp_path)


def blocked_after_broadcast(h):
    """Job 1 broadcast, then another transaction uses its nonce -> blocked_nonce. Job 2 waits."""
    h.chain.auto_mine = False
    k1 = h.task(1)
    k2 = h.task(2, "b_before.jpg", "b_after.jpg", worker=W2)
    h.run()
    h.chain.use_nonce_elsewhere()
    h.run()
    assert h.state(k1) == "blocked_nonce"
    return k1, k2


def actions(h):
    return {it["job_key"]: it["action"] for it in tool.plan(h.storage, h.chain, h.settings.signer_address)}


def test_foreign_nonce_needs_explicit_expire_then_signing_resumes(h):
    k1, k2 = blocked_after_broadcast(h)
    items = tool.plan(h.storage, h.chain, h.settings.signer_address)
    assert [it["action"] for it in items] == ["expire_foreign"]
    assert tool.apply(h.storage, items, expire_foreign=False) == []
    assert h.state(k1) == "blocked_nonce"
    assert tool.apply(h.storage, items, expire_foreign=True) == [f"{k1} -> expired"]
    assert h.state(k1) == "expired" and "operator" in h.job(k1)["last_error"]
    h.chain.auto_mine = True
    h.restart()
    h.run()
    assert h.state(k2) == "confirmed"
    signed_for_1 = [s for s in h.chain.signed if s["id"] == 1]
    assert len(signed_for_1) == 1  # job 1 never signed a second transaction (V-J3 step 6)


def test_blocked_before_signing_goes_back_to_queued(h):
    h.chain.pending_extra = 1
    key = h.task(1)
    h.run()
    assert h.state(key) == "blocked_nonce" and h.chain.signed == []
    assert actions(h) == {key: "queued"}
    tool.apply(h.storage, tool.plan(h.storage, h.chain, h.settings.signer_address), expire_foreign=False)
    h.chain.pending_extra = 0
    h.restart()
    h.run()
    assert h.state(key) == "confirmed" and len(h.chain.signed) == 1


def broadcast_then_stale_block(h, refunded=False):
    """Our transaction is mined, but the job was marked blocked_nonce before the reconciler saw the
    receipt (for example a stale nonce read). The nonce was used by OUR transaction."""
    h.chain.auto_mine = False
    key = h.task(1)
    h.run()
    assert h.state(key) == "broadcast"
    if refunded:
        h.chain.tasks[1]["status"] = "Refunded"  # the transaction reverts
    h.chain.include(h.job(key)["raw_tx"])
    h.set_state(key, "blocked_nonce")
    return key


def test_our_transaction_mined_needs_no_change(h):
    key = broadcast_then_stale_block(h)
    assert actions(h) == {key: "none"}
    assert tool.apply(h.storage, tool.plan(h.storage, h.chain, h.settings.signer_address), True) == []
    h.run()  # the event reader sees the VerdictRecorded event: finalize confirms the job (V-J4)
    assert h.state(key) == "confirmed" and len(h.chain.signed) == 1


def test_our_transaction_reverted_becomes_reverted(h):
    key = broadcast_then_stale_block(h, refunded=True)
    assert actions(h) == {key: "reverted"}
    tool.apply(h.storage, tool.plan(h.storage, h.chain, h.settings.signer_address), False)
    assert h.state(key) == "reverted"


def test_unused_nonce_goes_back_to_broadcast_and_resends_the_same_transaction(h):
    h.chain.auto_mine = False
    key = h.task(1)
    h.run()
    raw = h.job(key)["raw_tx"]
    h.set_state(key, "blocked_nonce")  # for example a stale read; the nonce is still free
    assert actions(h) == {key: "broadcast"}
    tool.apply(h.storage, tool.plan(h.storage, h.chain, h.settings.signer_address), False)
    h.chain.auto_mine = True
    h.restart()
    h.run()
    assert h.state(key) == "confirmed" and h.job(key)["raw_tx"] == raw and len(h.chain.signed) == 1


def test_no_blocked_jobs_gives_no_actions(h):
    h.task(1)
    h.run()
    assert tool.plan(h.storage, h.chain, h.settings.signer_address) == []
