"""Worker loop, transition table, reconciler and reports (V-J, V-19..V-22).

Tests V-T5, V-T6, V-T7, V-T8, V-T10, V-T11 (job part), V-T17 (worker part), V-T18, V-T19, V-T22.
A "restart" is a new Worker on the same data folder (the old one is dropped without cleanup).
"""

import json

import pytest

from app import jobs as jobs_mod
from app.chain import Chain, ReadonlyError
from app.events import EventReader
from app.jobs import AI_NOT_DONE, Worker, build_report, finalize, job_key
from app.limits import Admission, Budget
from app.storage import Storage
from app.vision import Vision
from tests.conftest import (
    ESCROW,
    FIXTURES,
    FakeChain,
    FakeModel,
    fixture_hash,
    http_error,
    inproc_decoder,
    inproc_model_input,
    make_settings,
    model_reply,
    store_file,
)

W1, W2 = "0x" + "22" * 20, "0x" + "33" * 20


@pytest.fixture(autouse=True)
def fast_timers(monkeypatch):
    monkeypatch.setattr(jobs_mod, "RECEIPT_WAIT_S", 0)
    monkeypatch.setattr(jobs_mod, "RECONCILE_INTERVAL_S", 0)


class StorageReadiness:
    """Ready unless a job is in blocked_nonce (V-R10); the other V-R items are tested in M3."""

    def __init__(self, storage):
        self.storage = storage
        self.ready = True

    def snapshot(self):
        return {"ready": self.ready and self.storage.count_jobs(("blocked_nonce",)) == 0}

    refresh = snapshot


class H:
    """One verifier (live by default) on a fake chain."""

    def __init__(self, tmp_path, chain=None, model=None, **env):
        env.setdefault("VERIFIER_MODE", "live")
        self.settings = make_settings(tmp_path, **env)
        s = self.settings
        self.storage = Storage(s.data_dir, s.deployment_id, s.max_storage_mb, s.start_block)
        self.storage.open()
        self.chain = chain or FakeChain()
        self.model = model or FakeModel(default=model_reply(confidence=86))
        self.reader = EventReader(s, self.storage, self.chain, admit=Admission(s))
        self.reader.startup()
        self.readiness = StorageReadiness(self.storage)
        self.worker = self.new_worker()

    def new_worker(self) -> Worker:
        budget = Budget(self.settings, self.storage)
        vision = Vision(self.settings, budget.count_model_call, client=self.model)
        return Worker(
            self.settings, self.storage, self.chain, vision, budget, self.readiness,
            decode=inproc_decoder, model_input=inproc_model_input,
        )

    def restart(self) -> Worker:
        self.worker = self.new_worker()
        self.worker.startup()
        return self.worker

    def scan(self):
        while self.reader.scan_once():
            pass

    def run(self, max_steps=40):
        """Steps until the worker has nothing to do at once. Scans events between steps."""
        for _ in range(max_steps):
            self.scan()
            if self.worker.step() is not True:
                self.scan()
                return
        raise AssertionError("worker did not settle")

    def file(self, name) -> str:
        return store_file(self.storage, FIXTURES / name)

    def task(self, task_id, before="a_before.jpg", after="a_after.jpg", worker=W1, attempt=1, upload_after=True,
             **kw) -> str:
        """Create, accept and submit. Returns the job key. `upload_after=False`: hash committed, no file."""
        bh = self.file(before) if before else "0x" + "ab" * 32
        ah = (self.file(after) if upload_after else fixture_hash(after)) if after else "0x" + "cd" * 32
        self.chain.create_task(task_id, bh, **kw)
        self.chain.accept_task(task_id, worker)
        self.chain.submit_proof(task_id, attempt, ah)
        self.scan()
        return job_key(self.settings.deployment_id, task_id, attempt, ah)

    def job(self, key):
        with self.storage.read() as c:
            return c.execute("SELECT * FROM jobs WHERE job_key = ?", (key,)).fetchone()

    def state(self, key) -> str:
        return self.job(key)["state"]

    def evaluation(self, key) -> dict:
        return json.loads(self.job(key)["evaluation_json"])

    def claim(self, task_id, attempt=1):
        with self.storage.read() as c:
            return c.execute(
                "SELECT state FROM claims WHERE task_id = ? AND attempt = ? AND role = 'after'", (task_id, attempt)
            ).fetchone()["state"]

    def set_state(self, key, state, **fields):
        fields["state"] = state
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.storage.tx() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE job_key = ?", (*fields.values(), key))


@pytest.fixture
def h(tmp_path):
    return H(tmp_path)


# ---------------------------------------------------------------- normal path


def test_good_pair_is_confirmed_with_one_transaction(h):
    key = h.task(1)
    h.run()
    assert h.state(key) == "confirmed"
    assert h.claim(1) == "accepted"
    assert h.chain.tasks[1]["status"] == "Approved"
    assert len(h.chain.signed) == 1 and h.chain.signed[0]["pass"] is True and h.chain.signed[0]["score"] == 86
    ev = h.evaluation(key)
    assert ev["reason"] == "AI check: task complete (confidence 86)"
    assert [c["id"] for c in ev["checks"]] == [f"V-C{i}" for i in range(1, 8)]
    assert ev["configVersion"] == h.settings.config_version and ev["model"] == "test-model"
    assert len(h.model.calls) == 1


@pytest.mark.parametrize(
    "reply, reason",
    [
        (model_reply(same=False, confidence=90), "AI check: not the same place"),
        (model_reply(completed=False, confidence=90), AI_NOT_DONE),
        (model_reply(confidence=69), "AI check: confidence 69 is below 70"),
    ],
)
def test_V19_V21_fail_reasons(tmp_path, reply, reason):
    h = H(tmp_path, model=FakeModel([reply]))
    key = h.task(1)
    h.run()
    ev = h.evaluation(key)
    assert (ev["pass"], ev["reason"]) == (False, reason)
    assert ev["score"] == json.loads(reply)["confidence"]  # V-20
    assert h.chain.tasks[1]["status"] == "Accepted" and h.claim(1) == "rejected"


def test_V20_score_zero_without_model_result(h):
    key = h.task(1, before=None)  # before file never uploaded
    h.chain.set_time(h.chain.tasks[1]["reviewBy"] - 170)  # <= 180 s: continue without files
    h.run()
    ev = h.evaluation(key)
    assert (ev["pass"], ev["score"]) == (False, 0)
    assert ev["reason"] == "Photo file missing or does not match the commitment"
    assert {c["result"] for c in ev["checks"][1:]} == {"skipped"}  # V-T1 with V-C7
    assert h.model.calls == [] and h.state(key) == "confirmed"


# ---------------------------------------------------------------- V-T5 serial gate


def test_VT5_unsettled_verdict_blocks_next_job_then_reuse_fails(h):
    h.chain.auto_mine = False
    k1 = h.task(1, "a_before.jpg", "a_after.jpg")
    k2 = h.task(2, "b_before.jpg", "a_after.jpg", worker=W2)  # same photo X, other task
    h.run()
    assert h.state(k1) == "broadcast" and h.state(k2) == "queued"
    for _ in range(3):  # reconcile rounds: no receipt yet, B never starts
        h.run()
    assert h.state(k2) == "queued" and len(h.model.calls) == 1 and len(h.chain.signed) == 1
    h.chain.include(h.job(k1)["raw_tx"])
    h.chain.auto_mine = True
    h.run()
    assert h.state(k1) == "confirmed" and h.claim(1) == "accepted"
    h.run()
    ev = h.evaluation(k2)
    assert ev["pass"] is False and ev["reason"] == "Same photo as the accepted proof of task #1"
    assert h.state(k2) == "confirmed" and h.claim(2) == "rejected"
    assert len(h.model.calls) == 1  # B failed V-C2: no vision call


def test_VJ1_never_two_unsettled_jobs(h):
    h.chain.auto_mine = False
    keys = [h.task(i, "a_before.jpg", "b_after.jpg" if i % 2 else "a_after.jpg") for i in (1, 2, 3)]
    for _ in range(5):
        h.run()
        assert h.storage.count_jobs(("evaluating", "evaluated", "signed", "broadcast")) <= 1
    assert [h.state(k) for k in keys] == ["broadcast", "queued", "queued"]


# ---------------------------------------------------------------- V-T6 superseded


def test_VT6_queued_job_superseded_signs_nothing(h):
    key = h.task(1)
    h.chain.tasks[1].update(status="Accepted")  # the task moved on
    h.run()
    assert h.state(key) == "superseded" and h.chain.signed == [] and h.model.calls == []


def test_VT6_evaluated_job_superseded_at_signing(h):
    key = h.task(1)
    h.worker._start(h.job(key))
    assert h.state(key) == "evaluated"
    h.chain.submit_proof(1, 2, "0x" + "99" * 32)  # a newer attempt
    h.run()
    assert h.state(key) == "superseded" and h.chain.signed == []


def test_VJ_evaluated_requeued_when_accepted_claim_appears(h):
    k2 = h.task(2, "b_before.jpg", "a_after.jpg")
    h.worker._start(h.job(k2))
    assert h.evaluation(k2)["pass"] is True
    # Meanwhile the same photo became an accepted proof of task 1 (for example a chain event).
    with h.storage.tx() as c:
        c.execute("INSERT INTO claims VALUES (1, 1, 'after', ?, 'accepted', 'e0')", (h.job(k2)["proof_hash"],))
    h.worker.step()
    assert h.state(k2) == "queued" and h.job(k2)["evaluation_json"] is None and h.chain.signed == []
    h.run()
    assert h.evaluation(k2)["reason"] == "Same photo as the accepted proof of task #1"


def test_VJ_config_version_change_requeues(tmp_path):
    h = H(tmp_path)
    key = h.task(1)
    h.worker._start(h.job(key))
    h2 = H(tmp_path, chain=h.chain, MIN_CONFIDENCE="90")  # restart with a new decision setting
    h2.restart()
    h2.worker.step()
    assert h2.state(key) == "queued" and h2.chain.signed == []


# ---------------------------------------------------------------- V-T7 crash at each state


def _one_tx(h, key):
    assert h.state(key) == "confirmed"
    assert len(h.chain.signed) == 1
    assert len({r for r in h.chain.sent}) == 1  # only the one raw transaction was ever sent
    assert h.chain.tasks[1]["status"] == "Approved"


def test_VT7_crash_in_queued(h):
    key = h.task(1)
    h.restart()
    assert h.state(key) == "queued"
    h.run()
    _one_tx(h, key)


def test_VT7_crash_in_awaiting_files(tmp_path):
    h = H(tmp_path)
    key = h.task(1, upload_after=False)  # proof hash committed, file not uploaded yet
    h.run()
    assert h.state(key) == "awaiting_files"
    h.restart()
    assert h.state(key) == "awaiting_files"
    h.file("a_after.jpg")  # the upload arrives
    h.set_state(key, "awaiting_files", next_try_at=0)  # 15 s later
    h.run()
    _one_tx(h, key)


def test_VT7_crash_in_evaluating(h):
    key = h.task(1)
    h.set_state(key, "evaluating", eval_rounds=1)
    h.restart()
    assert h.state(key) == "queued" and h.job(key)["eval_rounds"] == 1  # rounds kept
    h.run()
    _one_tx(h, key)


def test_VT7_crash_in_evaluated(h):
    key = h.task(1)
    h.worker._start(h.job(key))
    assert h.state(key) == "evaluated"
    h.restart()
    h.run()
    _one_tx(h, key)
    assert len(h.model.calls) == 1  # not evaluated again


def test_VT7_crash_in_signed(h):
    key = h.task(1)
    h.worker._start(h.job(key))
    h.worker._sign(h.job(key))
    assert h.state(key) == "signed" and h.chain.sent == []
    raw = h.job(key)["raw_tx"]
    h.restart()
    h.run()
    _one_tx(h, key)
    assert h.chain.sent == [raw]


def test_VT7_crash_in_broadcast(h):
    h.chain.auto_mine = False
    key = h.task(1)
    h.run()
    assert h.state(key) == "broadcast"
    h.restart()
    h.chain.auto_mine = True
    h.run()  # reconcile at once: same raw_tx sent again
    _one_tx(h, key)


def test_VT7_crash_after_inclusion_before_db_update(h):
    h.chain.auto_mine = False
    key = h.task(1)
    h.run()
    h.chain.include(h.job(key)["raw_tx"])  # mined while the verifier was down
    h.restart()
    h.run()
    _one_tx(h, key)


def test_VJ3_receipt_not_finalized_waits(h):
    key = h.task(1)
    h.chain.receipt_final = False
    h.chain.finalized_lag = 3  # the verdict block is not finalized yet
    h.run()
    assert h.state(key) == "broadcast"
    h.run()
    # The nonce is used (by our own transaction), but this is not blocked_nonce: the receipt exists.
    assert h.state(key) == "broadcast" and h.chain.nonce(h.chain.verifier_address) == 1
    h.chain.receipt_final = True
    h.chain.finalized_lag = 0
    h.run()
    assert h.state(key) == "confirmed"


def test_VJ3_event_reader_can_finalize_first(h):
    h.chain.auto_mine = False
    key = h.task(1)
    h.run()
    h.chain.include(h.job(key)["raw_tx"])
    h.scan()  # VerdictRecorded seen by the event reader (V-E3)
    assert h.state(key) == "confirmed"
    h.run()
    assert h.state(key) == "confirmed" and len(h.chain.signed) == 1


# ---------------------------------------------------------------- V-T8 reverted and blocked_nonce


def test_VT8_receipt_status_0_is_reverted(h):
    h.chain.auto_mine = False
    key = h.task(1)
    h.run()
    h.chain.tasks[1]["status"] = "Refunded"  # the transaction now reverts
    h.chain.include(h.job(key)["raw_tx"])
    h.run()
    j = h.job(key)
    assert j["state"] == "reverted" and "Refunded" in j["last_error"]
    assert h.claim(1) == "pending"  # a revert is not a verdict


def test_VT8_used_nonce_without_receipt_blocks_all_signing(h):
    h.chain.auto_mine = False
    k1 = h.task(1)
    k2 = h.task(2, "b_before.jpg", "b_after.jpg", worker=W2)
    h.run()
    assert h.state(k1) == "broadcast"
    h.chain.use_nonce_elsewhere()
    h.run()
    assert h.state(k1) == "blocked_nonce"
    h.chain.auto_mine = True
    for _ in range(3):
        h.run()
    assert h.state(k2) == "queued" and len(h.chain.signed) == 1  # no other job signs


def test_VJ_pending_nonce_differs_blocks(h):
    h.chain.pending_extra = 1
    key = h.task(1)
    h.run()
    assert h.state(key) == "blocked_nonce" and h.chain.signed == []


def test_VJ3_resend_until_reviewBy_then_expire(h):
    h.chain.auto_mine = False
    key = h.task(1)
    h.run()
    h.run()
    assert len(h.chain.sent) >= 2 and len(set(h.chain.sent)) == 1  # same raw_tx, never a new one
    review = h.chain.tasks[1]["reviewBy"]
    h.chain.set_time(review + 10)
    sent = len(h.chain.sent)
    h.run()
    assert h.state(key) == "broadcast" and len(h.chain.sent) == sent  # waits, no resend
    h.chain.set_time(review + 600)
    h.run()
    assert h.state(key) == "expired" and len(h.chain.signed) == 1


def test_VJ3_send_error_is_kept_and_retried(h):
    h.chain.send_error = ConnectionError("rpc down")
    key = h.task(1)
    h.run()
    assert h.state(key) == "broadcast" and "rpc down" in h.job(key)["last_error"]
    h.chain.send_error = None
    h.run()
    assert h.state(key) == "confirmed"


# ---------------------------------------------------------------- V-J5 model retries, V-T10


def test_VJ5_three_failed_rounds_model_failed(tmp_path):
    h = H(tmp_path, model=FakeModel(["bad"] * 6))
    key = h.task(1)
    h.run()
    j = h.job(key)
    assert (j["state"], j["eval_rounds"]) == ("model_retry", 1)
    assert j["next_try_at"] - j["updated_at"] == 30
    h.set_state(key, "model_retry", next_try_at=0)
    h.run()
    j = h.job(key)
    assert (j["state"], j["eval_rounds"], j["next_try_at"] - j["updated_at"]) == ("model_retry", 2, 60)
    h.set_state(key, "model_retry", next_try_at=0)
    h.run()
    assert h.state(key) == "model_failed" and h.chain.signed == []  # not a fail verdict
    assert len(h.model.calls) == 6


def test_VJ5_retry_waits_for_next_try_at(tmp_path):
    h = H(tmp_path, model=FakeModel(["bad", "bad"], default=model_reply()))
    key = h.task(1)
    h.run()
    h.run()
    assert h.state(key) == "model_retry" and len(h.model.calls) == 2


def test_VT10_401_blocked_config_no_retry_then_restart_test_call(tmp_path):
    model = FakeModel([http_error(401)])
    h = H(tmp_path, model=model)
    key = h.task(1)
    h.run()
    assert h.state(key) == "blocked_config" and len(model.calls) == 1
    for _ in range(3):
        h.run()
    assert len(model.calls) == 1  # no automatic retry
    model.replies = [http_error(401)]
    h.restart()  # test call fails: stays blocked
    assert h.state(key) == "blocked_config"
    model.default = model_reply()
    h.restart()  # test call succeeds
    assert h.state(key) == "queued"
    h.run()
    assert h.state(key) == "confirmed"


# ---------------------------------------------------------------- V-T11, V-J2 expiry, awaiting_files


def test_VT11_remaining_below_150_expires_without_model_call(h):
    key = h.task(1)
    h.chain.set_time(h.chain.tasks[1]["reviewBy"] - 149)
    h.run()
    assert h.state(key) == "expired" and h.model.calls == [] and h.chain.signed == []


def test_VT11_remaining_150_is_not_expired(h):
    key = h.task(1)
    h.chain.set_time(h.chain.tasks[1]["reviewBy"] - 150)
    h.run()
    assert h.state(key) == "confirmed"


def test_VJ_awaiting_files_polls_every_15_s(h):
    key = h.task(1, upload_after=False)
    h.run()
    j = h.job(key)
    assert j["state"] == "awaiting_files" and j["next_try_at"] - j["updated_at"] == 15
    h.run()
    assert h.state(key) == "awaiting_files" and h.model.calls == []


def test_VJ_evaluated_with_less_than_60_s_expires(h):
    key = h.task(1)
    h.worker._start(h.job(key))
    h.chain.set_time(h.chain.tasks[1]["reviewBy"] - 59)
    h.run()
    assert h.state(key) == "expired" and h.chain.signed == []


# ---------------------------------------------------------------- budgets (V-A5, V-J7)


def test_VJ7_model_budget_reached_job_waits_queued(tmp_path):
    h = H(tmp_path, MAX_MODEL_CALLS_PER_DAY="1")  # a round needs 2
    key = h.task(1)
    h.run()
    assert h.state(key) == "queued" and h.model.calls == []


def test_VA5_verdict_tx_cap_jobs_wait(tmp_path):
    h = H(tmp_path, MAX_VERDICT_TX_PER_DAY="1")
    k1 = h.task(1)
    k2 = h.task(2, "b_before.jpg", "b_after.jpg", worker=W2)
    h.run()
    h.run()
    assert h.state(k1) == "confirmed" and h.state(k2) == "queued" and len(h.chain.signed) == 1


# ---------------------------------------------------------------- V-T17 readonly


def test_VT17_readonly_worker_never_signs(tmp_path):
    h = H(tmp_path, VERIFIER_MODE="readonly")
    key = h.task(1)
    for _ in range(3):
        h.run()
    assert h.state(key) == "queued" and h.chain.signed == [] and h.model.calls == []


def test_VT17_readonly_chain_cannot_sign_or_send():
    chain = Chain("http://127.0.0.1:8545", ESCROW, can_sign=False)
    with pytest.raises(ReadonlyError):
        chain.build_verdict("0x" + "11" * 32, 0, 1, 1, "0x" + "22" * 32, True, 80, "x")
    with pytest.raises(ReadonlyError):
        chain.send_raw("0x00")


# ---------------------------------------------------------------- V-T18 canonical order


def _history(chain_obj, h, first_task):
    """Two tasks submit the same photo. `first_task` submits first."""
    x = h.file("a_after.jpg")
    for t, before in ((1, "a_before.jpg"), (2, "b_before.jpg")):
        chain_obj.create_task(t, h.file(before))
        chain_obj.accept_task(t, W1 if t == 1 else W2)
    for t in (first_task, 3 - first_task):
        chain_obj.submit_proof(t, 1, x)
    return x


@pytest.mark.parametrize("first_task", [1, 2])
def test_VT18_verify_timing_does_not_change_order(tmp_path, first_task):
    results = []
    for variant in ("scan_each", "scan_once"):
        h = H(tmp_path / variant)
        chain = h.chain
        if variant == "scan_each":
            x = h.file("a_after.jpg")
            for t, before in ((1, "a_before.jpg"), (2, "b_before.jpg")):
                chain.create_task(t, h.file(before))
                h.reader.wake()
                h.scan()
                chain.accept_task(t, W1 if t == 1 else W2)
                h.scan()
            for t in (first_task, 3 - first_task):
                chain.submit_proof(t, 1, x)
                h.scan()  # a POST /verify after each submission
        else:
            _history(chain, h, first_task)
            h.scan()  # one late POST /verify
        for _ in range(4):
            h.run()
        results.append({t: (chain.tasks[t]["status"], h.claim(t)) for t in (1, 2)})
    assert results[0] == results[1]
    winner, loser = first_task, 3 - first_task
    assert results[0][winner] == ("Approved", "accepted") and results[0][loser] == ("Accepted", "rejected")


# ---------------------------------------------------------------- V-T19 reports per attempt


def test_VT19_reports_are_separate_for_each_attempt(tmp_path):
    h = H(tmp_path, model=FakeModel([model_reply(completed=False, confidence=40)], default=model_reply(confidence=88)))
    h.task(1, "a_before.jpg", "b_after.jpg")
    h.run()
    assert h.chain.tasks[1]["status"] == "Accepted"
    second = h.file("a_after.jpg")
    h.chain.submit_proof(1, 2, second)
    h.run()
    r1 = build_report(h.storage, h.settings.deployment_id, 1, 1)
    r2 = build_report(h.storage, h.settings.deployment_id, 1, 2)
    assert (r1["attemptResult"], r1["evaluation"]["pass"], r1["evaluation"]["score"]) == ("rejected", False, 40)
    assert (r2["attemptResult"], r2["evaluation"]["pass"], r2["evaluation"]["score"]) == ("approved", True, 88)
    assert r1["proofHash"] != r2["proofHash"] == second
    assert r1["settlement"]["txHashes"] != r2["settlement"]["txHashes"]
    assert r2["jobState"] == "confirmed" and r2["settlement"]["state"] == "confirmed"
    assert build_report(h.storage, h.settings.deployment_id, 1, 3) is None


def test_V22_report_shape(h):
    h.task(1)
    h.run()
    r = build_report(h.storage, h.settings.deployment_id, 1, 1)
    assert set(r) == {"deploymentId", "taskId", "attempt", "proofHash", "jobState", "attemptResult", "evaluation",
                      "settlement", "createdAt"}
    assert set(r["evaluation"]) == {"pass", "score", "reason", "evaluatedBlock", "model", "configVersion", "checks"}
    assert set(r["settlement"]) == {"state", "txHashes", "error", "conflict"}
    assert r["createdAt"].endswith("Z")


def test_V22_evaluation_null_until_evaluated(h):
    h.task(1)
    r = build_report(h.storage, h.settings.deployment_id, 1, 1)
    assert r["jobState"] == "queued" and r["evaluation"] is None and r["settlement"]["state"] == "not_sent"


# ---------------------------------------------------------------- V-T22 finalize


def test_VT22_finalize_updates_job_and_claim_together(h):
    h.chain.auto_mine = False
    key = h.task(1)
    h.run()
    with h.storage.tx() as c:
        finalize(c, h.settings.deployment_id, 1, 1, h.job(key)["proof_hash"], True, 86, "0xabc", "e9")
    assert h.state(key) == "confirmed" and h.claim(1) == "accepted"
    assert "0xabc" in json.loads(h.job(key)["tx_hashes"])


def test_VT22_crash_inside_finalize_changes_nothing(h):
    h.chain.auto_mine = False
    key = h.task(1)
    h.run()
    before = (h.state(key), h.claim(1), h.job(key)["tx_hashes"])
    with pytest.raises(RuntimeError):
        with h.storage.tx() as c:
            finalize(c, h.settings.deployment_id, 1, 1, h.job(key)["proof_hash"], True, 86, "0xabc", "e9")
            raise RuntimeError("crash")
    assert (h.state(key), h.claim(1), h.job(key)["tx_hashes"]) == before


def test_VT22_different_score_sets_conflict(h):
    h.chain.auto_mine = False
    key = h.task(1)
    h.run()
    with h.storage.tx() as c:
        finalize(c, h.settings.deployment_id, 1, 1, h.job(key)["proof_hash"], True, 55, "0xabc", "e9")
    assert h.job(key)["conflict"] == 1


def test_V04_last_error_never_holds_secrets(tmp_path):
    h = H(tmp_path)
    h.chain.send_error = ConnectionError(f"boom {h.settings.signer_key} {h.settings.vision_api_key}")
    key = h.task(1)
    h.run()
    err = h.job(key)["last_error"]
    assert h.settings.signer_key not in err and h.settings.vision_api_key not in err
