import { settings } from "./settings";
import { defineChain } from "viem";

/**
 * F-03: Monad testnet, the only chain of the app.
 * Network facts from https://docs.monad.xyz/developer-essentials/testnet (chain ID 10143, currency MON).
 * Multicall3 is deployed on Monad testnet at the standard address (checked with `cast code` on 2026-10-05).
 */
export const monadTestnet = defineChain({
  id: settings.chainId,
  name: "Monad Testnet",
  nativeCurrency: { name: "Monad", symbol: "MON", decimals: 18 },
  rpcUrls: { default: { http: [settings.rpcUrl] } },
  blockExplorers: { default: { name: "Monad explorer", url: settings.explorerUrl } },
  contracts: {
    multicall3: { address: "0xcA11bde05977b3631167028862bE2a173976CA11", blockCreated: 251449 },
  },
  testnet: true,
});

export const FAUCET_URL = "https://faucet.monad.xyz";

export const explorerTxUrl = (hash: string) => `${settings.explorerUrl}/tx/${hash}`;
export const explorerAddressUrl = (address: string) => `${settings.explorerUrl}/address/${address}`;
