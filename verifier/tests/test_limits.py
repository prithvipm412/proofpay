"""Admission and caps (V-A1..V-A5, V-T20), HTTP rate limits (V-A6, V-T21), endpoints V-11..V-13."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app, prepare
from app.storage import counter, now_s, utc_day
from tests.conftest import FIXTURES, FakeChain, FakeModel, inproc_decoder, make_settings, model_reply
from tests.test_jobs import H, W1, W2

P1, P2 = "0x" + "11" * 20, "0x" + "44" * 20


def states(h) -> dict[int, tuple[str, str | None]]:
    with h.storage.read() as c:
        return {r["task_id"]: (r["state"], r["last_error"]) for r in c.execute("SELECT * FROM jobs")}


def submit(h, task_id, poster=P1, worker=W1):
    h.chain.create_task(task_id, "0x" + f"{task_id:064x}", poster=poster)
    h.chain.accept_task(task_id, worker)
    h.chain.submit_proof(task_id, 1, "0x" + f"{task_id + 1000:064x}")


# ---------------------------------------------------------------- V-T20


@pytest.mark.parametrize(
    "env, setup, reason",
    [
        ({"MAX_JOBS_PER_DAY": "2"}, [(1, P1, W1), (2, P2, W2), (3, P1, W2)], "daily_job_cap"),
        ({"MAX_JOBS_PER_POSTER": "2"}, [(1, P1, W1), (2, P1, W2), (3, P1, W1)], "poster_daily_cap"),
        ({"MAX_JOBS_PER_WORKER": "2"}, [(1, P1, W1), (2, P2, W1), (3, P2, W1)], "worker_daily_cap"),
        ({"MAX_QUEUE": "2"}, [(1, P1, W1), (2, P2, W2), (3, P1, W2)], "queue_full"),
    ],
)
def test_VT20_each_cap_gives_not_admitted(tmp_path, env, setup, reason):
    h = H(tmp_path, **env)
    for task_id, poster, worker in setup:
        submit(h, task_id, poster, worker)
    h.scan()
    s = states(h)
    assert s[1][0] == s[2][0] == "queued"
    assert s[3] == ("not_admitted", reason)
    # Move the two admitted jobs out of the way, then run: the not_admitted job gets nothing (V-A4).
    with h.storage.tx() as c:
        c.execute("UPDATE jobs SET state = 'superseded' WHERE task_id IN (1, 2)")
    h.run()
    assert states(h)[3] == ("not_admitted", reason)
    assert h.model.calls == [] and h.chain.signed == []


def test_VT20_allowlist_mode(tmp_path):
    h = H(tmp_path, ADMISSION_MODE="allowlist", ALLOWLIST=f"{P1},{W1}")
    submit(h, 1, P1, W1)
    submit(h, 2, P1, W2)  # worker not on the list
    submit(h, 3, P2, W1)  # poster not on the list
    h.scan()
    s = states(h)
    assert s[1] == ("queued", None)
    assert s[2] == s[3] == ("not_admitted", "not_on_allowlist")


def test_VA_unknown_parties_fail_closed(tmp_path):
    h = H(tmp_path)
    h.chain.create_task(1, "0x" + "01" * 32)
    h.chain.tasks[1].update(status="Accepted")  # TaskAccepted event missing from the history
    h.chain.submit_proof(1, 1, "0x" + "02" * 32)
    h.scan()
    assert states(h)[1] == ("not_admitted", "unknown_parties")


def test_VA2_counters_only_for_admitted_jobs(tmp_path):
    h = H(tmp_path, MAX_JOBS_PER_DAY="1")
    submit(h, 1, P1, W1)
    submit(h, 2, P2, W2)
    h.scan()
    with h.storage.read() as c:
        day = utc_day(now_s())
        assert counter(c, day, "jobs") == 1
        assert counter(c, day, f"jobs_poster:{P2}") == 0


def test_VA_rebuild_does_not_admit_again(tmp_path):
    h = H(tmp_path, MAX_JOBS_PER_DAY="1")
    submit(h, 1, P1, W1)
    h.scan()
    h.reader.start_rebuild()
    h.scan()
    assert states(h)[1] == ("queued", None)  # the job row is kept; no second admission


def test_VM7_model_calls_are_counted(tmp_path):
    h = H(tmp_path, model=FakeModel(["bad"], default=model_reply()))
    h.task(1)
    h.run()
    with h.storage.read() as c:
        assert counter(c, utc_day(now_s()), "model_calls") == 2
        assert counter(c, utc_day(now_s()), "verdict_tx") == 1


# ---------------------------------------------------------------- endpoints and rate limits


@pytest.fixture
def api(tmp_path):
    chain = FakeChain()
    services = prepare(make_settings(tmp_path), chain, decoder=inproc_decoder)
    with TestClient(create_app(services, start_threads=False)) as c:
        yield c, services, chain


def test_V11_verify_wakes_reader_and_never_makes_a_job(api):
    c, services, chain = api
    woke = []
    services.reader.wake = lambda: woke.append(1)
    r = c.post("/verify", json={"taskId": 7})
    assert r.status_code == 200 and r.json() == {"jobKey": None, "state": None}
    assert woke == [1]
    assert services.storage.count_jobs(("queued",)) == 0 and chain.sent == []


def test_V11_verify_returns_current_job(api):
    c, services, chain = api
    chain.create_task(1, "0x" + "01" * 32)
    chain.accept_task(1)
    chain.submit_proof(1, 1, "0x" + "02" * 32)
    while services.reader.scan_once():
        pass
    body = c.post("/verify", json={"taskId": 1}).json()
    assert body["state"] == "queued" and body["jobKey"].endswith(":1:1:0x" + "02" * 32)


@pytest.mark.parametrize("body", [{}, {"taskId": 0}, {"taskId": "1"}, {"taskId": True}, [1]])
def test_V11_verify_bad_body_400(api, body):
    assert api[0].post("/verify", json=body).status_code == 400


def test_V12_V13_attempt_reports(api):
    c, services, chain = api
    chain.create_task(1, "0x" + "01" * 32)
    chain.accept_task(1)
    chain.submit_proof(1, 1, "0x" + "02" * 32)
    while services.reader.scan_once():
        pass
    lst = c.get("/tasks/1/attempts").json()
    assert [r["attempt"] for r in lst] == [1] and lst[0]["jobState"] == "queued"
    assert c.get("/tasks/1/attempts/1").json()["attemptResult"] == "none"
    assert c.get("/tasks/1/attempts/2").status_code == 404
    assert c.get("/tasks/9/attempts").json() == []


def test_VT21_verify_rate_limit_429(api):
    c = api[0]
    codes = [c.post("/verify", json={"taskId": 1}).status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429


def test_VT21_upload_rate_limit_429(api):
    c = api[0]
    data = (FIXTURES / "a_after.jpg").read_bytes()
    codes = [c.post("/upload", files={"file": ("p.jpg", data, "image/jpeg")}).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
