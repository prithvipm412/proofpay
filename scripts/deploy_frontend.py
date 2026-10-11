"""Deploy the frontend to Vercel with the public verifier URL (M8 task 2).

Usage (from the repo root, after `yarn vercel:login`):
    verifier/.venv/bin/python scripts/deploy_frontend.py --verifier-url https://<your-domain>.ngrok-free.app
    verifier/.venv/bin/python scripts/deploy_frontend.py --verifier-url https://... --preview   # not production

1. Checks that the verifier URL answers GET /health through ngrok with the same deployment ID as
   packages/nextjs/.env.local (NEXT_PUBLIC_CHAIN_ID : NEXT_PUBLIC_ESCROW_ADDRESS).
2. Runs the template deploy (`yarn vercel --prod`) with every NEXT_PUBLIC_* setting passed as a
   build setting, because Next.js puts these values into the build and Vercel does not upload
   .env.local. NEXT_PUBLIC_VERIFIER_URL is replaced by --verifier-url. The values are not printed
   (they are public anyway: they end up in the browser bundle).
The first run asks Vercel's setup questions; the defaults are correct (scope: your account; link to
an existing project: no; project name: proofpay; directory: ./).
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
from urllib.parse import urlparse

import httpx

REPO = pathlib.Path(__file__).resolve().parent.parent
ENV_LOCAL = REPO / "packages" / "nextjs" / ".env.local"


def read_env(path: pathlib.Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    p = argparse.ArgumentParser(description="Deploy the ProofPay frontend to Vercel")
    p.add_argument("--verifier-url", required=True, help="public https URL of the verifier (ngrok)")
    p.add_argument("--preview", action="store_true", help="preview deployment instead of production")
    args = p.parse_args()

    url = args.verifier_url.strip().rstrip("/")
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or u.path not in ("", "/"):
        print("STOP: --verifier-url must be an https origin, for example https://abc-123.ngrok-free.app")
        return 2
    if not ENV_LOCAL.exists():
        print(f"STOP: {ENV_LOCAL} is missing.")
        return 2
    env = {k: v for k, v in read_env(ENV_LOCAL).items() if k.startswith("NEXT_PUBLIC_")}
    needed = ["NEXT_PUBLIC_PRIVY_APP_ID", "NEXT_PUBLIC_ESCROW_ADDRESS", "NEXT_PUBLIC_CHAIN_ID",
              "NEXT_PUBLIC_RPC_URL", "NEXT_PUBLIC_EXPLORER_URL"]
    missing = [k for k in needed if not env.get(k)]
    if missing:
        print(f"STOP: empty in .env.local: {', '.join(missing)}")
        return 2
    if env["NEXT_PUBLIC_CHAIN_ID"] != "10143":
        print("STOP: NEXT_PUBLIC_CHAIN_ID must be 10143 (Monad testnet).")
        return 2
    env["NEXT_PUBLIC_VERIFIER_URL"] = url

    want = f"10143:{env['NEXT_PUBLIC_ESCROW_ADDRESS'].lower()}"
    try:
        r = httpx.get(f"{url}/health", headers={"ngrok-skip-browser-warning": "1"}, timeout=20)
        health = r.json()
    except Exception as exc:
        print(f"STOP: {url}/health does not answer with JSON ({type(exc).__name__}). Is ngrok running?")
        return 1
    if health.get("deploymentId") != want:
        print(f"STOP: the verifier serves {health.get('deploymentId')}, the frontend is for {want}.")
        return 1
    print(f"Verifier OK: {url} serves {want} (ready: {health.get('ready')}, mode: {health.get('mode')}).")
    if health.get("mode") != "live":
        print("Note: the verifier is not in live mode; checks will not be saved on chain.")

    cmd = ["yarn", "vercel"] + ([] if args.preview else ["--prod"])
    for key in sorted(env):
        cmd += ["--build-env", f"{key}={env[key]}"]
    print(f"Running: yarn vercel {'' if args.preview else '--prod '}with {len(env)} NEXT_PUBLIC_* build settings")
    proc = subprocess.run(cmd, cwd=REPO)
    if proc.returncode != 0:
        print("Vercel deploy failed. Read the Vercel output above.")
        return proc.returncode
    print("\nNext steps (PROGRESS.md, Needs owner):")
    print("  1. Copy the Production URL that Vercel printed (https://<name>.vercel.app).")
    print("  2. cd verifier && .venv/bin/python scripts/setup_env.py --add-cors-origin https://<name>.vercel.app")
    print("  3. Restart the verifier. 4. Add the URL in the Privy dashboard (allowed domains).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
