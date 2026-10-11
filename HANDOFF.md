# Handoff — 2026-10-11

Rules: `AGENTS.md` (= `CLAUDE.md`). Full log: `PROGRESS.md`. Owner target: submit 13 Oct 2026 before 12:00 IST.

## Current state

- M0–M4: DONE. Contract deployed and verified: `10143:0x0f8ae1306c78fcf7bf998f8b8ee88add1bd7a0cf`.
- M5: IN PROGRESS. Frontend part (F-01..F-09, F-12, F-20..F-23) is done and checked by the owner (task #2 from the browser). Calibration is not closed.
- M6: the owner said to start it in parallel with M5 (2026-10-11). No M6 code is written yet. The agent only read the code.

## Left in M5

1. The owner shoots the last 9 pairs today: master-bed second visit, 6 new places (morning), second visits to 2 of them (evening). They go into `~/Desktop/phone-photos-raw`.
2. Copy only the new files (check with SHA-256) into `verifier-data/calibration/<place>/<occasion>_before|after.jpg`. Group them by what the photo shows. Pair them in time order. Morning and evening are separate visits (`oct11am` / `oct11pm`). Add a `small-` prefix only for pairs that the owner names. Add each file to `verifier-data/calibration/SOURCES.txt`. The 14 earlier phone photos are already copied (21 pairs, 18 places now).
3. Run `calibrate.py` (command below). Choose the thresholds on the calibration set only. Report raw counts on the validation set. Apply V-C11.
4. Write `docs/calibration.md` (it does not exist yet). The owner approves the values. Mark M5 DONE.

## Left in M6

F-10 (full task detail), F-11 (My tasks), F-13 (submit proof with camera), F-14 (role buttons), F-15 (job state messages), F-16/F-16a, F-17 (poll every 3 s, back off on error), F-18 (sentence for each C-25 error; `utils/proofpay/txErrors.ts` now shows only the error name), F-19, and the test on the owner's phone (360 px, camera, MPO check).
Existing code to build on: `app/tasks/[id]/page.tsx` (first version), `hooks/proofpay/useEscrowTx.ts` (send one call), `hooks/proofpay/useTasks.ts`, `utils/proofpay/verifier.ts` (upload), `components/WithdrawBanner.tsx` (F-06 is already done).
Verifier report endpoints: `GET /tasks/{id}/attempts` and `GET /tasks/{id}/attempts/{n}` (V-22 format, `verifier/app/jobs.py` `build_report`). `POST /verify {"taskId": n}`.

## Commands (macOS, zsh, from the repo root)

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

- The verifier is STOPPED and far behind. Checkpoint 68,483,075; chain 70,028,849 (2026-10-11), about 1.55 M blocks. At about 112 blocks/s the catch-up takes about 4 h, and `ready` stays false (V-R7) until it ends. Task #2 has no claim yet. Each batch is 1 `get_logs` + 1 `get_block`, and the latency limits the speed (`verifier/app/events.py` `scan_once`). Option (b) "parallel getLogs within 50 rps" is DECIDED but not written. Plan: fetch about 8 batches at the same time with a rate cap of about 25 rps, then save them in block order, one transaction per batch with its checkpoint (V-E2, V-T15 MUST still pass). Then start the verifier and let it catch up before the M6 browser test.
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
