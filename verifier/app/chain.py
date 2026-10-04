"""Contract reads and event decoding (V-R1..V-R4, V-E). Signing and broadcast come in M4 (V-X).

All functions here block on RPC calls. Callers run them in background threads, never on the
API event loop (V-02).
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any

from web3 import Web3
from web3._utils.events import get_event_data

ABI_PATH = pathlib.Path(__file__).resolve().parent / "abi" / "ProofPayEscrow.json"
READ_EVENTS = ("TaskCreated", "ProofSubmitted", "VerdictRecorded")  # V-E3
RPC_TIMEOUT_S = 10

STATUS_NAMES = ("Open", "Accepted", "Submitted", "Approved", "Disputed", "Paid", "Refunded")


def load_abi() -> list[dict]:
    return json.loads(ABI_PATH.read_text())


def hx(value: bytes) -> str:
    """bytes -> "0x" + lowercase hex."""
    return "0x" + bytes(value).hex()


def event_id(block_number: int, tx_index: int, log_index: int) -> str:
    """V-S5: zero-padded so that text order equals chain order."""
    return f"{block_number:012d}:{tx_index:06d}:{log_index:06d}"


@dataclass(frozen=True)
class Block:
    number: int
    hash: str
    timestamp: int


@dataclass(frozen=True)
class ChainEvent:
    name: str
    block_number: int
    tx_index: int
    log_index: int
    tx_hash: str
    args: dict[str, Any]

    @property
    def event_id(self) -> str:
        return event_id(self.block_number, self.tx_index, self.log_index)


def _norm_arg(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return hx(value)
    return value


class Chain:
    """Reads from the RPC node for one deployment."""

    def __init__(self, rpc_url: str, escrow_address: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": RPC_TIMEOUT_S}))
        self.abi = load_abi()
        self.address = Web3.to_checksum_address(escrow_address)
        self.contract = self.w3.eth.contract(address=self.address, abi=self.abi)
        self._event_abis = {
            item["name"]: item for item in self.abi if item.get("type") == "event" and item["name"] in READ_EVENTS
        }
        self._topics = {}
        for name, item in self._event_abis.items():
            sig = f"{name}({','.join(i['type'] for i in item['inputs'])})"
            self._topics[hx(Web3.keccak(text=sig))] = name

    # ------------------------------------------------------------ blocks

    def chain_id(self) -> int:
        return int(self.w3.eth.chain_id)

    def block(self, ident: int | str) -> Block:
        b = self.w3.eth.get_block(ident)
        return Block(int(b["number"]), hx(b["hash"]), int(b["timestamp"]))

    def safe_head(self, method: str, offset: int) -> Block:
        """V-E1: the newest block that the event reader trusts."""
        if method == "finalized":
            return self.block("finalized")
        latest = int(self.w3.eth.block_number)
        return self.block(max(0, latest - offset))

    def latest_block_number(self) -> int:
        return int(self.w3.eth.block_number)

    # ------------------------------------------------------------ events

    def events(self, from_block: int, to_block: int) -> list[ChainEvent]:
        """TaskCreated, ProofSubmitted and VerdictRecorded in [from_block, to_block], in chain order."""
        logs = self.w3.eth.get_logs(
            {
                "address": self.address,
                "fromBlock": from_block,
                "toBlock": to_block,
                "topics": [list(self._topics)],
            }
        )
        out = []
        for log in logs:
            if log.get("removed"):
                continue
            name = self._topics.get(hx(log["topics"][0]))
            if name is None:
                continue
            data = get_event_data(self.w3.codec, self._event_abis[name], log)
            out.append(
                ChainEvent(
                    name=name,
                    block_number=int(log["blockNumber"]),
                    tx_index=int(log["transactionIndex"]),
                    log_index=int(log["logIndex"]),
                    tx_hash=hx(log["transactionHash"]),
                    args={k: _norm_arg(v) for k, v in data["args"].items()},
                )
            )
        out.sort(key=lambda e: e.event_id)
        return out

    # ------------------------------------------------------------ contract reads

    def code_exists(self) -> bool:
        return len(self.w3.eth.get_code(self.address)) > 0

    def verifier(self) -> str:
        return Web3.to_checksum_address(self.contract.functions.verifier().call())

    def task_count(self) -> int:
        return int(self.contract.functions.taskCount().call())

    def dispute_window(self) -> int:
        return int(self.contract.functions.disputeWindow().call())

    def review_grace(self) -> int:
        return int(self.contract.functions.reviewGrace().call())

    def balance(self, address: str) -> int:
        return int(self.w3.eth.get_balance(Web3.to_checksum_address(address)))

    def get_task(self, task_id: int) -> dict[str, Any]:
        t = self.contract.functions.getTask(task_id).call()
        keys = (
            "poster",
            "worker",
            "amount",
            "beforeHash",
            "proofHash",
            "createdAt",
            "submitBy",
            "reviewBy",
            "disputeUntil",
            "attempts",
            "score",
            "status",
            "title",
            "description",
        )
        task = {k: _norm_arg(v) for k, v in zip(keys, t)}
        task["status"] = STATUS_NAMES[int(task["status"])]
        return task
