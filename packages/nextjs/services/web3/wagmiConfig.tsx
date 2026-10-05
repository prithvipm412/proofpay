import { createConfig } from "@privy-io/wagmi";
import { http } from "viem";
import { monadTestnet } from "~~/utils/proofpay/chain";

/** F-03: Monad testnet is the only chain. Privy supplies the wallet connector (F-01). */
export const wagmiConfig = createConfig({
  chains: [monadTestnet],
  transports: { [monadTestnet.id]: http(monadTestnet.rpcUrls.default.http[0]) },
  pollingInterval: 1_000,
  ssr: true,
});
