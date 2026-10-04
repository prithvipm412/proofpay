"""Shared test helpers: valid settings, a fake chain, storage and fixture uploads.

Unit tests never touch a real chain or a real model (section 10.16). The fake chain uses
anvil-like values (chain 31337) and the public default anvil verifier address only as a value.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil

import pytest

from app.chain import Block, ChainEvent, STATUS_NAMES
from app.config import load_settings
from app.images import ReceivedFile, decode_file, sha256_file
from app.storage import Storage

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
DISTANCES = json.loads((FIXTURES / "distances.json").read_text())

ESCROW = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
# Public default anvil account #1 key (section 4 exception: local tests only).
ANVIL_KEY_1 = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
ANVIL_ADDR_1 = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
START_BLOCK = 100


def base_env(tmp_path: pathlib.Path, **overrides: str) -> dict[str, str]:
    env = {
        "CHAIN_ID": "31337",
        "MONAD_RPC_URL": "http://127.0.0.1:8545",
        "ESCROW_ADDRESS": ESCROW,
        "START_BLOCK": str(START_BLOCK),
        "VERIFIER_PRIVATE_KEY": ANVIL_KEY_1,
        "VISION_API_KEY": "test-key",
        "VISION_BASE_URL": "https://example.invalid/v1/",
        "VISION_MODEL": "test-model",
        "VISION_REASONING_EFFORT": "",
        "CORS_ORIGINS": "http://localhost:3000,https://proofpay.vercel.app",
        "DATA_ROOT": str(tmp_path / "data"),
        "MIN_CONFIDENCE": "70",
        "NEAR_HARD": "4",
        "NEAR_WARN": "10",
        "VERIFIER_MODE": "readonly",
        "ADMISSION_MODE": "open",
        "ALLOWLIST": "",
        "MAX_JOBS_PER_POSTER": "10",
        "MAX_JOBS_PER_WORKER": "10",
        "MAX_JOBS_PER_DAY": "150",
        "MAX_MODEL_CALLS_PER_DAY": "300",
        "MAX_VERDICT_TX_PER_DAY": "150",
        "MAX_QUEUE": "50",
        "MIN_SIGNER_BALANCE": "0.05",
        "MAX_STORAGE_MB": "2000",
        "ADMISSION_UNTIL": "2099-01-01T00:00:00Z",
        "SAFE_HEAD_METHOD": "finalized",
    }
    env.update(overrides)
    return env


def make_settings(tmp_path: pathlib.Path, **overrides: str):
    return load_settings(base_env(tmp_path, **overrides))


def inproc_decoder(path, preview_path=None) -> dict:
    """decode_file without a child process: fast, for tests that are not about V-L5."""
    return decode_file(str(path), str(preview_path) if preview_path else None)


class FakeChain:
    """In-memory chain with the same read interface as app.chain.Chain."""

    def __init__(self, chain_id: int = 31337, verifier: str = ANVIL_ADDR_1, first_block: int = 1):
        self.cid = chain_id
        self.verifier_address = verifier
        self.balances: dict[str, int] = {verifier: 10**18}
        self.salt = "a"
        self.blocks: list[Block] = []
        self.events_list: list[ChainEvent] = []
        self.tasks: dict[int, dict] = {}
        self.finalized_lag = 0
        self.has_code = True
        self.max_range = 100  # the testnet RPC limit
        self.calls_events: list[tuple[int, int]] = []
        self.fail_events_at: int | None = None
        self.t0 = 1_791_000_000
        self.mine(first_block + START_BLOCK + 5)

    def _hash(self, n: int) -> str:
        return "0x" + hashlib.sha256(f"{self.salt}:{n}".encode()).hexdigest()

    def mine(self, count: int = 1, seconds_each: float = 0.4) -> Block:
        for _ in range(count):
            n = len(self.blocks)
            self.blocks.append(Block(n, self._hash(n), int(self.t0 + n * seconds_each)))
        return self.blocks[-1]

    def change_history_from(self, n: int) -> None:
        """Make every block from n on get a new hash (a different chain history)."""
        self.salt += "x"
        self.blocks = [b if b.number < n else Block(b.number, self._hash(b.number), b.timestamp) for b in self.blocks]

    def emit(self, name: str, **args) -> ChainEvent:
        b = self.mine()
        ev = ChainEvent(name, b.number, 0, 0, "0x" + hashlib.sha256(f"tx{b.number}".encode()).hexdigest(), args)
        self.events_list.append(ev)
        return ev

    # ---- Chain interface
    def chain_id(self) -> int:
        return self.cid

    def latest_block_number(self) -> int:
        return self.blocks[-1].number

    def block(self, ident) -> Block:
        if ident == "finalized":
            return self.blocks[-1 - self.finalized_lag]
        if ident == "latest":
            return self.blocks[-1]
        return self.blocks[int(ident)]

    def safe_head(self, method: str, offset: int) -> Block:
        if method == "finalized":
            return self.block("finalized")
        return self.blocks[max(0, len(self.blocks) - 1 - offset)]

    def events(self, start: int, end: int) -> list[ChainEvent]:
        assert end - start + 1 <= self.max_range, "eth_getLogs range above the RPC limit"
        self.calls_events.append((start, end))
        if self.fail_events_at is not None and start <= self.fail_events_at <= end:
            raise ConnectionError("RPC down")
        return [e for e in self.events_list if start <= e.block_number <= end]

    def code_exists(self) -> bool:
        return self.has_code

    def verifier(self) -> str:
        return self.verifier_address

    def task_count(self) -> int:
        return len(self.tasks)

    def dispute_window(self) -> int:
        return 60

    def review_grace(self) -> int:
        return 300

    def balance(self, address: str) -> int:
        return self.balances.get(address, 0)

    def get_task(self, task_id: int) -> dict:
        return dict(self.tasks[task_id])

    # ---- helpers for tests
    def create_task(self, task_id: int, before_hash: str, created_at: int | None = None) -> ChainEvent:
        ev = self.emit(
            "TaskCreated",
            id=task_id,
            poster="0x" + "11" * 20,
            amount=5 * 10**16,
            beforeHash=before_hash,
            submitBy=self.t0 + 10_000,
            reviewBy=self.t0 + 10_300,
        )
        self.tasks[task_id] = {
            "beforeHash": before_hash,
            "proofHash": "0x" + "00" * 32,
            "createdAt": created_at if created_at is not None else self.blocks[-1].timestamp,
            "attempts": 0,
            "status": STATUS_NAMES[0],
            "title": f"Task {task_id}",
            "description": "Fixture task",
        }
        return ev

    def submit_proof(self, task_id: int, attempt: int, proof_hash: str) -> ChainEvent:
        self.tasks[task_id].update(proofHash=proof_hash, attempts=attempt, status="Submitted")
        return self.emit("ProofSubmitted", id=task_id, attempt=attempt, proofHash=proof_hash)

    def verdict(self, task_id: int, attempt: int, proof_hash: str, passed: bool, score: int = 80) -> ChainEvent:
        return self.emit(
            "VerdictRecorded", id=task_id, attempt=attempt, proofHash=proof_hash, **{"pass": passed},
            score=score, reason="test",
        )


@pytest.fixture
def chain() -> FakeChain:
    return FakeChain()


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def storage(settings) -> Storage:
    s = Storage(settings.data_dir, settings.deployment_id, settings.max_storage_mb, settings.start_block)
    s.open()
    return s


def store_file(storage: Storage, src: pathlib.Path, decoder=inproc_decoder) -> str:
    """Put a file through the upload path (V-09) without HTTP. Returns "0x" + SHA-256."""
    tmp = storage.tmp / f"copy-{src.name}"
    shutil.copyfile(src, tmp)
    return storage.store_upload(ReceivedFile(tmp, sha256_file(tmp), tmp.stat().st_size), decoder)


def fixture_hash(name: str) -> str:
    return "0x" + sha256_file(FIXTURES / name)
