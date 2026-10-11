# Demo script (3 minutes)

Demo deployment: `10143:0x0f8ae1306c78fcf7bf998f8b8ee88add1bd7a0cf` (dispute window 60 s, review grace
300 s, arbitration timeout 3600 s). Record on the public URL (Vercel) with the phone for the camera step.
Show every time skip with an on-screen caption. Do not hide time skips.

## Before the recording (about 30 min)

1. Verifier ready: `curl -s -H "ngrok-skip-browser-warning: 1" https://<name>.ngrok-free.app/health`
   shows `"ready": true`. Keep the Mac awake (`caffeinate -ims` in the verifier command) and on power.
2. Three email accounts, each logged in once on the public URL, each with test MON (faucet link in the
   header): **Poster** (about 0.3 MON), **Worker** (about 0.1 MON), **Second worker** (about 0.1 MON).
3. Photos with no faces and no private places: a bench (or similar public spot) with litter (before),
   and the same spot after the litter is picked up (taken live in step 4). Do a practice pair first.
4. **Prepared expired task (step 9):** at least 15 minutes before recording, the Poster makes a task
   with "Demo: 10 minutes", the Second worker accepts it and does nothing. After 10 minutes its time
   to send proof has ended. Do not refund it before the recording.
5. **Prepared second task (step 8):** the Poster makes a second open task (any before photo, 24 h).
6. Browser zoom so that the report card fits on screen; close other tabs. Phone screen recording on.

## Script

| Time | Screen | Say / do |
| --- | --- | --- |
| 0:00–0:15 | Landing page | "ProofPay: centralized escrow with automated visual checks and duplicate-evidence detection, on Monad. A poster locks MON for a small real-world task; a worker sends a photo; a verifier checks it; the worker gets paid seconds later." |
| 0:15–0:35 | Log in (Poster) | Step 1. Log in with an email address. "No seed phrase: Privy makes an embedded wallet at login." Show the header: short address and MON balance. |
| 0:35–1:00 | Post a task | Step 2. Title "Remove the litter around this bench", before photo, 0.05 MON, "Demo: 10 minutes". Show the progress lines and the new task page. "The photo hash is committed on chain; the MON is now in the contract." |
| 1:00–1:15 | Log in (Worker), task board | Step 3. Other browser / phone: log in with the Worker email, open the task, push **Accept task**. |
| 1:15–1:40 | Phone camera | Step 4. Take the after photo with the phone camera and push **Submit proof**. "The worker can close the page; the verifier reads the proof from the chain." |
| 1:40–2:05 | Report card | Step 5. Checks appear: integrity, exact reuse, near reuse, seen elsewhere, same as before, capture time, AI vision. "The verdict — pass, score, reason — is written to the contract by the verifier." Open **Details for this attempt** to show the verdict transaction. |
| 2:05–2:20 | Caption: **"60 seconds later"** | Step 6. Any user pushes **Release payment**. "The dispute window is over; anyone can release. The worker has the MON." Show the balance change. |
| 2:20–2:30 | Explorer | Step 7. Open the release transaction on testnet.monadvision.com. "Monad: low fees and fast confirmation make a 5-cent task practical — the worker is paid seconds after the verdict." |
| 2:30–2:45 | Second worker, prepared task | Step 8. The Second worker accepts the prepared second task and submits **the same after photo** from step 4. Report card: red **Duplicate photo** — "This exact photo was already accepted as proof for task #N." "One photo can approve only one task. The MON stays in the contract." |
| 2:45–2:58 | Caption: **"A task from earlier"** | Step 9. Poster opens the prepared expired task: "The time to send proof has ended." Push **Refund**; show the MON back. |
| 2:58–3:00 | Landing | "The verifier key and the arbiter are trusted — this is a hackathon trust assumption. Code, contract and limits are in the README." |

## If something goes wrong during the recording

- **Report stays at "In line for checking" or "Delayed — trying again"**: the model API can return
  HTTP 500; the verifier retries after 30 s and 60 s. Wait, or cut and show a caption "about 1 minute
  later".
- **"Checking is paused by the operator"**: stop; read `/health` `failed` and the verifier log.
- **Step 8 shows "Nearly the same photo" (V-C3) instead of "Duplicate photo" (V-C2)**: the browser
  re-encoded the photo, so the bytes differ (re-saved copies measured at pHash distance 0 to 2, which
  still fails V-C3). For the exact-reuse demo, the Second worker must pick the SAME file from the
  phone gallery (for example the same phone in a private tab logged in as the Second worker); do not
  take a new photo and do not send it through a messenger app first.
- **Release refused**: the 60 s window has not ended yet ("You can release the payment when the
  countdown ends"). Wait for the countdown.
