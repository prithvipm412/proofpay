# ProofPay progress

## Milestones
| ID | Goal | Day | Status | Date done |
| --- | --- | --- | --- | --- |
| M0 | Setup and external checks | 1 (4 Oct) | IN PROGRESS | |
| M1 | Contract part 1 + disposable deploy | 1 (4 Oct) | TODO | |
| M2 | Contract complete | 2 (5 Oct) | TODO | |
| M3 | Verifier part 1 | 3 (6 Oct) | TODO | |
| M4 | Verifier part 2 | 4 (7 Oct) | TODO | |
| M5 | Frontend part 1 + calibration | 5 (8 Oct) | TODO | |
| M6 | Frontend part 2 | 6 (9 Oct) | TODO | |
| M7 | Trust features + recovery | 7 (10 Oct) | TODO | |
| M8 | Public deploy + README | 8 (11 Oct) | TODO | |
| M9 | Release gate + demo | 9 (12 Oct) | TODO | |
| M10 | Submit | 10 (13 Oct) | TODO | |

## External checks (M0)
- Cutoff time and time zone:
- Qwen bounty rules:
- Model ID:
- Privy App ID ready (yes/no):
- Verifier host with persistent volume:
- Safe-head method (V-E1): `finalized` block tag (SAFE_HEAD_METHOD=finalized). Monad docs (reference/json-rpc/overview, monad-arch/consensus/block-states): `finalized` = Finalized state, "Irreversible without a hard fork". Tested 2026-10-04: `cast block finalized --rpc-url https://testnet-rpc.monad.xyz` works; finalized was 6 blocks behind latest (68104183 vs 68104189, about 2 s at 300 ms blocks).
- Monad testnet facts (docs/developer-essentials/testnet): chain ID 10143, RPC https://testnet-rpc.monad.xyz (QuickNode, 50 rps), explorers https://testnet.monadvision.com and https://testnet.monadscan.com, faucet https://faucet.monad.xyz.

## Versions (M0)
- Template commit: 14fa9c893ef747df2446cc3f2c08b55a58f867c0 (monad-developers/scaffold-monad-foundry, branch main)
- Foundry / Node / Next.js / Python: forge/anvil/cast 1.7.1-monad-v1.0.0 (bb49277) / Node v20.20.2, Yarn 3.2.3 / Next.js 15.2.5 (wagmi 2.15.6, viem 2.31.1, RainbowKit 2.2.7) / Python 3.11.15 via uv 0.12.23
- Submodules: forge-std 77041d2, openzeppelin-contracts e4f7021, solidity-bytes-utils f4413cd
- Verifier pins so far: openai==3.24.0, pillow==12.3.0

## Deployments
| Date | Deployment ID | START_BLOCK | Settings | Deploy tx | Verified | Note |
| --- | --- | --- | --- | --- | --- | --- |

## Important transactions
| Date | What | Tx hash |
| --- | --- | --- |

## Decisions
- (2026-10-04) Owner instruction: start a fresh git history. The first commit is an orphan commit that holds the template snapshot at 14fa9c893ef747df2446cc3f2c08b55a58f867c0 plus the M0 changes. The template is kept as the remote `upstream`. Credit is in README.md. No published history was rewritten: the project repo starts with this commit.
- (2026-10-04) Template bug: `scaffold.config.ts` used `chains.monad_testnet`, which viem 2.31.1 does not export (it exports `monadTestnet`). The page gave HTTP 500. Changed to `chains.monadTestnet`. M5 replaces this with our own `defineChain` (F-03).
- (2026-10-04) LICENSE (MIT) added. It keeps the BuidlGuidl template copyright line, as MIT requires. The template file LICENCE stays unchanged.

## Open problems
- (2026-10-04) V-E2 says scan up to 2000 blocks per batch. The QuickNode testnet RPC limits `eth_getLogs` to a 100-block range (tested: 1000 blocks gives error -32614 "eth_getLogs is limited to a 100 range"). At 300 ms blocks, 100 blocks is about 30 s. Next action: ask the owner at M3 to approve a batch size of 100 (or another RPC).
