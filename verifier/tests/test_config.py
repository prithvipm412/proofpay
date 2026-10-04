"""V-T13: settings validation (V-CFG1..V-CFG7)."""

import pytest

from app.config import ConfigError, load_settings
from tests.conftest import ANVIL_ADDR_1, ANVIL_KEY_1, base_env


def bad(tmp_path, **overrides) -> str:
    with pytest.raises(ConfigError) as exc:
        load_settings(base_env(tmp_path, **overrides))
    return str(exc.value)


def test_valid_settings_load(tmp_path):
    s = load_settings(base_env(tmp_path))
    assert s.chain_id == 31337
    assert s.signer_address == ANVIL_ADDR_1
    assert s.deployment_id == "31337:0x5fbdb2315678afecb367f032d93f642f64180aa3"
    assert s.data_dir.name == "31337-0x5fbdb2315678afecb367f032d93f642f64180aa3"
    assert s.min_signer_balance_wei == 5 * 10**16


@pytest.mark.parametrize("chain_id", ["143", "1", "0", "abc"])
def test_VT13_chain_id_rejected(tmp_path, chain_id):
    assert "CHAIN_ID" in bad(tmp_path, CHAIN_ID=chain_id)


def test_VT13_mainnet_rejected_even_with_testnet_rpc(tmp_path):
    msg = bad(tmp_path, CHAIN_ID="143", MONAD_RPC_URL="https://testnet-rpc.monad.xyz")
    assert "143" in msg


@pytest.mark.parametrize("url", ["https://testnet-rpc.monad.xyz", "http://10.0.0.5:8545", "http://127.0.0.1:9999"])
def test_VT13_local_chain_with_remote_rpc_rejected(tmp_path, url):
    assert "31337" in bad(tmp_path, CHAIN_ID="31337", MONAD_RPC_URL=url)


def test_testnet_with_public_rpc_ok(tmp_path):
    s = load_settings(base_env(tmp_path, CHAIN_ID="10143", MONAD_RPC_URL="https://testnet-rpc.monad.xyz"))
    assert s.chain_id == 10143


@pytest.mark.parametrize(
    "name,value",
    [
        ("ESCROW_ADDRESS", "0x1234"),
        ("ESCROW_ADDRESS", "not-an-address"),
        ("START_BLOCK", "-1"),
        ("START_BLOCK", "1.5"),
        ("MIN_CONFIDENCE", "0"),
        ("MIN_CONFIDENCE", "101"),
        ("NEAR_HARD", "-2"),
        ("NEAR_WARN", "65"),
        ("MAX_JOBS_PER_POSTER", "0"),
        ("MAX_JOBS_PER_WORKER", "-5"),
        ("MAX_JOBS_PER_DAY", "0"),
        ("MAX_MODEL_CALLS_PER_DAY", "0"),
        ("MAX_VERDICT_TX_PER_DAY", "0"),
        ("MAX_QUEUE", "0"),
        ("MAX_STORAGE_MB", "0"),
        ("MIN_SIGNER_BALANCE", "0"),
        ("MIN_SIGNER_BALANCE", "-0.1"),
        ("MIN_SIGNER_BALANCE", "abc"),
        ("CORS_ORIGINS", "localhost:3000"),
        ("CORS_ORIGINS", "ftp://example.com"),
        ("CORS_ORIGINS", "https://example.com/path"),
        ("VERIFIER_MODE", "maybe"),
        ("ADMISSION_MODE", "closed"),
        ("ADMISSION_UNTIL", "tomorrow"),
        ("ADMISSION_UNTIL", "2026-11-15T00:00:00"),
        ("SAFE_HEAD_METHOD", "safe"),
        ("VISION_BASE_URL", "http://example.com/v1"),
        ("VERIFIER_PRIVATE_KEY", "0x1234"),
    ],
)
def test_VT13_bad_values_rejected(tmp_path, name, value):
    assert name in bad(tmp_path, **{name: value})


def test_VT13_near_hard_above_near_warn_rejected(tmp_path):
    assert "NEAR_HARD" in bad(tmp_path, NEAR_HARD="11", NEAR_WARN="10")


def test_near_hard_minus_one_turns_hard_level_off(tmp_path):
    assert load_settings(base_env(tmp_path, NEAR_HARD="-1")).near_hard == -1


@pytest.mark.parametrize("name", ["CHAIN_ID", "MONAD_RPC_URL", "ESCROW_ADDRESS", "VISION_MODEL", "DATA_ROOT"])
def test_missing_setting_rejected(tmp_path, name):
    assert f"{name} is missing" in bad(tmp_path, **{name: ""})


def test_live_mode_needs_key_readonly_does_not(tmp_path):
    assert "VERIFIER_PRIVATE_KEY is missing" in bad(tmp_path, VERIFIER_MODE="live", VERIFIER_PRIVATE_KEY="")
    s = load_settings(base_env(tmp_path, VERIFIER_MODE="readonly", VERIFIER_PRIVATE_KEY=""))
    assert s.signer_address is None


def test_allowlist_mode_needs_valid_addresses(tmp_path):
    assert "ALLOWLIST is missing" in bad(tmp_path, ADMISSION_MODE="allowlist", ALLOWLIST="")
    assert "ALLOWLIST" in bad(tmp_path, ADMISSION_MODE="allowlist", ALLOWLIST="0xabc")
    s = load_settings(base_env(tmp_path, ADMISSION_MODE="allowlist", ALLOWLIST=f" {ANVIL_ADDR_1} "))
    assert s.allowlist == {ANVIL_ADDR_1.lower()}


def test_ollama_fallback_urls_accepted(tmp_path):
    for url in ("http://localhost:11434/v1", "http://127.0.0.1:11434/v1"):
        assert load_settings(base_env(tmp_path, VISION_BASE_URL=url)).vision_base_url == url


def test_safe_head_latest_minus_n(tmp_path):
    s = load_settings(base_env(tmp_path, SAFE_HEAD_METHOD="latest-minus-3"))
    assert (s.safe_head_method, s.safe_head_offset) == ("latest-minus-N", 3)


def test_all_problems_reported_together(tmp_path):
    msg = bad(tmp_path, CHAIN_ID="143", MIN_CONFIDENCE="0", MAX_QUEUE="0")
    assert "CHAIN_ID" in msg and "MIN_CONFIDENCE" in msg and "MAX_QUEUE" in msg


def test_V04_secrets_not_in_errors_or_repr(tmp_path):
    msg = bad(tmp_path, VERIFIER_PRIVATE_KEY="0x" + "zz" * 32, MIN_CONFIDENCE="0")
    assert "zz" * 32 not in msg
    s = load_settings(base_env(tmp_path))
    assert ANVIL_KEY_1 not in repr(s) and "test-key" not in repr(s)


def test_VCFG7_config_version_follows_decision_settings_only(tmp_path):
    a = load_settings(base_env(tmp_path))
    assert a.config_version == load_settings(base_env(tmp_path, MAX_QUEUE="7")).config_version
    for change in ({"MIN_CONFIDENCE": "71"}, {"NEAR_HARD": "3"}, {"NEAR_WARN": "11"}, {"VISION_MODEL": "other"}):
        assert load_settings(base_env(tmp_path, **change)).config_version != a.config_version
    assert a.config_version.startswith("0x") and len(a.config_version) == 66
