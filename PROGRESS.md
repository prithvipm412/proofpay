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
- Cutoff time and time zone: 14 Oct 2026, 03:59 UTC (= 13 Oct 11:59 PM US Eastern, 14 Oct 09:29 IST). Source (2026-10-04): owner, quoting other teams who quoted the rules. NOT yet confirmed by the owner on the hackathon dashboard. Owner target stays 13 Oct before 12:00 IST.
- Qwen bounty rules: not pursued (owner, 2026-10-04). Alibaba Cloud does not allow India as the account country, so no Model Studio key is possible. Do not select the Qwen bounty at M10.
- Model ID: `gemini-3-flash-preview` (Google Gemini API free tier, OpenAI-compatible base URL https://generativelanguage.googleapis.com/v1beta/openai/). Owner probe on 2026-10-04: `[OK]` in 5.8 s, but the reply was cut off ("The image features"), so the probe was improved (see Decisions). Final probe result: PENDING (needs the renamed lines in verifier/.env).
- Privy App ID ready (yes/no): PENDING (owner answer not given yet).
- Verifier host with persistent volume: the owner's Mac, exposed with an ngrok free static domain (set up at M8). The data folder stays on the Mac. The live demo works only while the Mac runs the verifier.
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
- (2026-10-04) Owner: budget is zero. Do not use any paid service. (Replaces the paid-host examples in section 12 M0 task 5.)
- (2026-10-04) Owner: vision model is the Gemini API free tier, not Qwen on Alibaba Cloud Model Studio (section 6 change). Base URL https://generativelanguage.googleapis.com/v1beta/openai/. The `openai` Python SDK stays.
- (2026-10-04) Owner: renamed settings DASHSCOPE_API_KEY -> VISION_API_KEY, DASHSCOPE_BASE_URL -> VISION_BASE_URL, QWEN_VL_MODEL -> VISION_MODEL everywhere (CLAUDE.md identifiers, verifier/.env.example, probe script). The prose in CLAUDE.md sections 6, 8 and 12 still names Qwen/Alibaba; this decision replaces it.
- (2026-10-04) Owner: thinking models. Gemini 3 Flash always thinks, and thinking tokens count toward the output token limit (Google docs: thinking page, "max_output_tokens ... including thought tokens"; OpenAI-compat page: "Reasoning cannot be turned off for ... 3 models"). Use the lowest level, `minimal` (supported by gemini-3-flash-preview per the thinking page), through the OpenAI parameter `reasoning_effort`. New optional setting `VISION_REASONING_EFFORT` (empty = the parameter is not sent). Raise V-M1 `max_tokens` from 200 to a value that always fits the full JSON verdict; the probe default is 1024. Final value is set from the probe's measured token use (see Model ID).
- (2026-10-04) Owner: HTTP 400 with an invalid-key message is a configuration error (V-M6, `blocked_config`). V-M6 already lists 400. Gemini returns HTTP 400 "API key not valid" for a bad key.
- (2026-10-04) Owner: MAX_MODEL_CALLS_PER_DAY MUST be below the Gemini free-tier daily limit for the model. Rule chosen by the agent: cap <= floor(RPD / 2), because V-A2 counts per UTC day but Google resets RPD at midnight Pacific time (rate-limits page), so two UTC days can fall in one Pacific day. Also count probe calls and V-M5 second calls. Value is set when the owner reads the RPD (see Open problems).
- (2026-10-04) Owner: fallback is local Ollama at http://localhost:11434/v1. It MUST work with only .env changes (VISION_BASE_URL, VISION_API_KEY=any non-empty text, VISION_MODEL, VISION_REASONING_EFFORT empty). Do not install it now. M3 settings validation (V-CFG) MUST accept http://localhost and http://127.0.0.1 for VISION_BASE_URL, and the model code MUST NOT send provider-specific parameters unless they are set in .env.
- (2026-10-04) Owner: verifier host is the owner's Mac with an ngrok free static domain (section 6 / M8 change). Data folder on the Mac.
- (2026-10-04) Owner: the README MUST say: demo photos are sent to Google's Gemini API free tier (use photos with no faces or private places), and the live demo works only while the owner's Mac runs the verifier. Google terms for unpaid services: content is used "to provide, improve, and develop Google products", human reviewers may read input and output, and "Do not submit sensitive, confidential, or personal information". Added to README.md now; keep it in the M8 README.
- (2026-10-04) Probe script `verifier/scripts/model_probe.py`: prints finish_reason, full reply and token use; PASS only IF finish_reason is "stop" and the reply names red, circle, blue and square; a second call uses the V-M2 system message and a V-M3 user message and PASS needs one complete JSON object with the four keys. Tested offline against a fake server (good, cut-off, HTTP 400 cases); the key is never printed.
- (2026-10-04) Owner instruction: start a fresh git history. The first commit is an orphan commit that holds the template snapshot at 14fa9c893ef747df2446cc3f2c08b55a58f867c0 plus the M0 changes. The template is kept as the remote `upstream`. Credit is in README.md. No published history was rewritten: the project repo starts with this commit.
- (2026-10-04) Template bug: `scaffold.config.ts` used `chains.monad_testnet`, which viem 2.31.1 does not export (it exports `monadTestnet`). The page gave HTTP 500. Changed to `chains.monadTestnet`. M5 replaces this with our own `defineChain` (F-03).
- (2026-10-04) LICENSE (MIT) added. It keeps the BuidlGuidl template copyright line, as MIT requires. The template file LICENCE stays unchanged.

## Open problems
- (2026-10-04) V-M1 says `temperature = 0`. Google's Gemini 3 guide: "For all Gemini 3 models, we strongly recommend keeping the temperature parameter at its default value of 1.0", and lower values "may lead to unexpected behavior, such as looping or degraded performance". The probe uses 0 (the spec value). Next action: owner decides at M4 (keep 0, or 1.0 for Gemini only through a .env setting).
- (2026-10-04) Model lifetime: gemini-3-flash-preview is a preview model. The Gemini models page (2026-10-04) seemed to list it under both "Preview" and "Previous models (Shut down)", but the owner's probe worked today. A preview can be shut down before or during judging (ADMISSION_UNTIL 2026-11-15). Next action: owner checks the model's status in AI Studio; a stable Flash model is a .env-only change.
- (2026-10-04) Gemini free-tier limits for this model are not published in the docs. The rate-limits page says limits "can be viewed in Google AI Studio". RPD resets at midnight Pacific time. Next action: owner reads RPM and RPD for gemini-3-flash-preview in AI Studio; agent sets MAX_MODEL_CALLS_PER_DAY = floor(RPD / 2) or less.
- (2026-10-04) V-E2 says scan up to 2000 blocks per batch. The QuickNode testnet RPC limits `eth_getLogs` to a 100-block range (tested: 1000 blocks gives error -32614 "eth_getLogs is limited to a 100 range"). At 300 ms blocks, 100 blocks is about 30 s. Next action: ask the owner at M3 to approve a batch size of 100 (or another RPC).
