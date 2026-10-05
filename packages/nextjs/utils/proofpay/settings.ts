import { parseSettings } from "./config";
import deployedContracts from "~~/contracts/deployedContracts";

// Next.js inlines each NEXT_PUBLIC_* value at build time only when the name is written out in full.
const parsed = parseSettings(
  {
    privyAppId: process.env.NEXT_PUBLIC_PRIVY_APP_ID,
    escrowAddress: process.env.NEXT_PUBLIC_ESCROW_ADDRESS,
    verifierUrl: process.env.NEXT_PUBLIC_VERIFIER_URL,
    chainId: process.env.NEXT_PUBLIC_CHAIN_ID,
    rpcUrl: process.env.NEXT_PUBLIC_RPC_URL,
    explorerUrl: process.env.NEXT_PUBLIC_EXPLORER_URL,
  },
  deployedContracts[10143]?.ProofPayEscrow?.address,
);

/** Valid only when `settingsErrors` is empty. The app shows the errors instead of the screens otherwise. */
export const settings = parsed.settings;
export const settingsErrors = parsed.errors;
