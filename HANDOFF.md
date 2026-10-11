# Handoff — 2026-10-12

Rules: `AGENTS.md` (= `CLAUDE.md`). Full log: `PROGRESS.md`. Owner target: submit 13 Oct 2026 before 12:00 IST.

## Current state (2026-10-12 about 03:30 IST, overnight session; owner asleep until about 9 AM)

- M0–M4: DONE. Contract deployed and verified: `10143:0x0f8ae1306c78fcf7bf998f8b8ee88add1bd7a0cf`.
- M5: IN PROGRESS. `docs/calibration.md` is written as a PROPOSAL (21 pairs, V-C11 PASS at NEAR_HARD 4); owner approval open.
- M6: code done; owner tests open (two accounts; phone test moved to M8).
- M7: IN PROGRESS. Code + local tests done (`trust_local.py` 9/9, `trust_testnet.py --rehearse` 9/9, demo 8/9 notices, V-M4 thought-block fix). Open: owner runs `verifier/scripts/trust_testnet.py` (PROGRESS Needs owner B).
- M8: IN PROGRESS. Done: backup/restore (V-B1, V-B2, V-T16) + first real backup (02:27); README; deploy helpers (`scripts/deploy_frontend.py`, `setup_env.py --add-cors-origin`); ngrok header + quota-friendly polling; `blocked_nonce.py`. Readonly restore check of the 02:27 backup: RESTORE CHECK OK (60 min rebuild; folder `verifier-data/restore-20261012-a/`). Open: ngrok, Vercel, Privy domains, phone test, admission choice, AI tools list (Needs owner A, C–F).
- M9 prep done: `docs/demo-script.md`, `docs/pitch.md`, `docs/brand/logo.png|svg`, `docs/brand/cover.png|svg`, `docs/release-gate.md` (15.1 evidence; testnet parts open).
- **The owner's to-do list is PROGRESS.md "Needs owner" (A to G, in order).** Every decision of this session is in PROGRESS "Decisions" (2026-10-12 entries).
- Running in the agent session: live verifier (port 8000, log `verifier-data/logs/verifier-live.log`), `yarn start` (port 3000, log `verifier-data/logs/next-dev.log`). They stop when the session ends.
- Tests now: forge 67, verifier pytest 337, frontend Vitest 92 (all pass); `next build` passes (`NEXT_DIST_DIR=.next-build yarn build`).
- Agent-only notes: the agent cannot read `~/Desktop` (macOS privacy prompt; an `ls` hangs). Do not run `yarn vercel <anything>`: the template script starts a deploy. Headless Chrome screenshots: `verifier-data/scratch/cdp_shot.py URL OUT.png W H WAIT` (DevTools protocol; plain `--screenshot` hangs because the page polls).

## Left for the agent after the owner acts

1. After Needs owner A1: IF the owner approves 4/10 and 21 pairs, mark M5 DONE (PROGRESS table, `docs/calibration.md` status line and checkboxes). IF new photos arrive: copy only new files (check SHA-256) into `verifier-data/calibration/<place>/<occasion>_before|after.jpg`, pair by content and time, `small-` prefix only when the owner names it, add to `SOURCES.txt`, run `calibrate.py`, update `docs/calibration.md`.
2. After B: copy the rows from `verifier-data/trust-testnet-<time>.json` into PROGRESS "Important transactions"; IF all PASS, mark M7 DONE (and the testnet parts of `docs/release-gate.md` rows 5 and 6). Then run C (restore of the 02:27 backup; about 1 h; must list the B tasks as "accepted after the backup").
3. After D/E: put the two URLs in README (table at the top, section 8/9 as needed) and PROGRESS (Deployments note / Versions). Push.
4. After A2/A3: README section 6 (admission) and section 10 (AI tools) final text.
5. After F/G: README section 6 last bullet (15.2 item 8), `docs/release-gate.md`, M8 and M9 status. M10: go through README 14.2 with the owner.

## Commands (macOS, zsh, from the repo root)## Commands (macOS, zsh, from the repo root)

```
# Verifier (reads verifier/.env; do not print it). Port 8000.
cd verifier && .venv/bin/python -m app.main
curl -s http://localhost:8000/health

# Frontend. Port 3000.
yarn start

# Tests
cd verifier && .venv/bin/python -m pytest -q
yarn workspace @se-2/nextjs test
cd packages/foundry && forge test
cd verifier && .venv/bin/python scripts/e2e_local.py        # starts its own anvil on 127.0.0.1:8545; nothing else may use port 8545

# Calibration (provisional thresholds)
cd verifier && .venv/bin/python scripts/calibrate.py --near-hard 4 --near-warn 10
```

## Open decisions (owner)

- NEAR_HARD / NEAR_WARN: provisional values 4 / 10 (owner, 2026-10-11). Final values come after the last photos and need the owner's approval (V-C11). Run 2 (21 pairs): V-C11 PASS at 4; the closest honest validation pair is 6.
- Same-day evening visit as a second visit: the agent answered YES on 2026-10-11, with three conditions: (1) the place is messed up again, with a new before and a new after; (2) shoot handheld from a normal place, without copying the morning framing; (3) a new place is a different scene, not another view of an existing place. The agent suggested shooting the master-bed second visit at night under the same light as 8 Oct (about 21:50), to get one repeat with the same light. Owner confirmation is not recorded.
- Schedule: M5 is about 3 days late. Section 13 cuts are not decided.

## Known issues

- Verifier catch-up: option (b) parallel fetches is written (commit afef315, about 500 to 600 blocks/s). A live verifier started inside an agent session stops when that session ends; the checkpoint is kept, so the next start continues from it. For long runs, the owner starts it in an own terminal. Check `ready` with /health before a browser test.
- Phone MPO photos: handle at M6 (F-13 converts to JPEG IF the phone gives MPO).
- The cutoff time is not confirmed on the dashboard. The model lifetime check is due before M8 (see PROGRESS Open problems).

## Do not change

- Section 4 safety rules: no private keys or `.env` content in code, logs, commits or chat; testnet 10143 only; no force push; no history rewrite.
- Only one live verifier for the deployment. Stop the old one before you start a new one. Use `readonly` for every other copy.
- The deployed contract and its address. `contracts/deployedContracts.ts` must match `NEXT_PUBLIC_ESCROW_ADDRESS`.
- The data folder `verifier-data/10143-0x0f8a…a0cf/` (claims, jobs, photos). Do not delete it or reset it.
- `verifier-data/calibration/` (git ignores it; the photos are not in the repo).
- Pinned versions (PROGRESS Versions): viem 2.56.5, wagmi 2.15.6, Privy 3.47.0 / 4.0.18.
- Do not change a test only to make it pass. Commit format: `M<n>: <change> (<IDs>)`.
