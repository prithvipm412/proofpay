"""The verifier's committed ABI copy must equal the Foundry build (9.7.6, V-R2)."""

import json
import pathlib

import pytest

from app.chain import load_abi

FOUNDRY_OUT = (
    pathlib.Path(__file__).resolve().parents[2] / "packages/foundry/out/ProofPayEscrow.sol/ProofPayEscrow.json"
)


@pytest.mark.skipif(not FOUNDRY_OUT.exists(), reason="run `forge build` in packages/foundry first")
def test_abi_copy_matches_foundry_build():
    assert load_abi() == json.loads(FOUNDRY_OUT.read_text())["abi"]


def test_abi_has_what_the_verifier_reads():
    names = {(i["type"], i.get("name")) for i in load_abi()}
    for fn in ("verifier", "taskCount", "disputeWindow", "reviewGrace", "getTask", "recordVerdict"):
        assert ("function", fn) in names
    for ev in ("TaskCreated", "ProofSubmitted", "VerdictRecorded"):
        assert ("event", ev) in names
