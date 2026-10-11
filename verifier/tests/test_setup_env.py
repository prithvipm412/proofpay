"""scripts/setup_env.py: fills only empty settings and keeps comments."""

import importlib.util
import pathlib

from app.config import read_env_file

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "setup_env.py"
spec = importlib.util.spec_from_file_location("setup_env", SCRIPT)
setup_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_env)


def test_fills_only_empty_values_and_keeps_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nCHAIN_ID=10143\nMIN_CONFIDENCE=\nNEAR_HARD=-1\nVISION_API_KEY=secret\n")
    changed = setup_env.set_values(env, setup_env.DEFAULTS, only_if_empty=True)
    values = read_env_file(env)
    assert values["MIN_CONFIDENCE"] == "70"  # was empty
    assert values["NEAR_HARD"] == "-1"  # kept
    assert values["VISION_API_KEY"] == "secret" and values["CHAIN_ID"] == "10143"
    assert "MIN_CONFIDENCE" in changed and "NEAR_HARD" not in changed
    assert env.read_text().startswith("# comment\n")
    assert env.stat().st_mode & 0o777 == 0o600


def test_replaces_signer_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("VERIFIER_PRIVATE_KEY=\n")
    setup_env.set_values(env, {"VERIFIER_PRIVATE_KEY": "0x" + "1" * 64}, only_if_empty=False)
    assert read_env_file(env)["VERIFIER_PRIVATE_KEY"] == "0x" + "1" * 64
    assert env.read_text().count("VERIFIER_PRIVATE_KEY") == 1


def run_main(monkeypatch, env, *args):
    monkeypatch.setattr("sys.argv", ["setup_env.py", "--env-file", str(env), *args])
    return setup_env.main()


def test_M8_add_cors_origin_appends_once_and_keeps_secrets(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("CORS_ORIGINS=http://localhost:3000\nVISION_API_KEY=secret\n")
    assert run_main(monkeypatch, env, "--add-cors-origin", "https://proofpay-xyz.vercel.app/") == 0
    assert read_env_file(env)["CORS_ORIGINS"] == "http://localhost:3000,https://proofpay-xyz.vercel.app"
    assert run_main(monkeypatch, env, "--add-cors-origin", "https://proofpay-xyz.vercel.app") == 0
    assert read_env_file(env)["CORS_ORIGINS"].count("vercel.app") == 1
    assert read_env_file(env)["VISION_API_KEY"] == "secret"
    assert "secret" not in capsys.readouterr().out


def test_M8_add_cors_origin_rejects_a_bad_origin(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("CORS_ORIGINS=http://localhost:3000\n")
    assert run_main(monkeypatch, env, "--add-cors-origin", "proofpay.vercel.app/path") == 1
    assert read_env_file(env)["CORS_ORIGINS"] == "http://localhost:3000"
