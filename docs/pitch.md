# Pitch (2 minutes)

About 260 words; read at a calm pace. Use these words for the claims; do not claim more.

## 1. Problem (0:00–0:25)

"Small real-world jobs — pick up litter, tidy a shared room, water the plants — are worth a dollar or
two. Paying a stranger for them is hard: the worker wants to know the money exists, the poster wants
to pay only for work that was done, and nobody wants to review every photo by hand or pay twice for
the same photo. And with a one-dollar task, a one-dollar fee or a payout that takes days kills it."

## 2. Solution (0:25–0:55)

"ProofPay is centralized escrow with automated visual checks and duplicate-evidence detection. The
poster logs in with an email, posts a task with a before photo and locks MON in the contract. The
worker sends an after photo. Our verifier checks that both photos match their on-chain commitments,
checks that the photo was not already accepted for another task, and asks a vision model whether the
work is done at the same place. The verdict goes to the contract. After a short dispute window anyone
can release the payment; a dispute goes to an arbiter; an absent arbiter means a refund."

## 3. Why Monad (0:55–1:15)

"Micro-tasks need very low fees and fast confirmation. On Monad testnet a verdict costs about 0.006 MON
and a release about 0.006 MON, blocks come every few hundred milliseconds, and the worker is paid
seconds after the dispute window ends. That makes a five-cent task practical."

## 4. Trust design and limits (1:15–1:45)

"We are honest about trust. The verifier key and the arbiter are trusted parties — this is a hackathon
trust assumption. A photo does not prove physical work, or who took it, or when. Only a photo with a
pass verdict on chain can block another task, so nobody can poison an honest worker's photo. The
verifier settles one verdict at a time, never signs two different verdicts for one job, and recovers
from a crash at any step with at most one transaction — we tested that on Monad testnet."

## 5. Next steps (1:45–2:00)

"Next: more than one verifier or a decentralized oracle instead of one key, an arbiter screen,
notifications, and a mainnet deployment. ProofPay — pay for real-world work, with checks you can read."

## Facts behind the numbers

- Fees: verdict 0.0064 MON, release 0.0058 MON, `createTask` from the browser 0.0351 MON (README
  section 3; Monad charges the gas limit).
- "Tested on Monad testnet": only after the M7 testnet run passes (PROGRESS "Needs owner" item B).
  IF it has not passed, remove that sentence.
