import { getAddress, isAddress } from "viem";

/** Frontend settings (CLAUDE.md section 8). */
export type Settings = {
  privyAppId: string;
  escrowAddress: `0x${string}`;
  verifierUrl: string;
  chainId: number;
  rpcUrl: string;
  explorerUrl: string;
};

export type RawSettings = {
  privyAppId?: string;
  escrowAddress?: string;
  verifierUrl?: string;
  chainId?: string;
  rpcUrl?: string;
  explorerUrl?: string;
};

/** F-03: Monad testnet is the only chain. Mainnet (143) is never allowed (section 4). */
export const MONAD_TESTNET_ID = 10143;

const httpUrl = (value: string | undefined): string | null => {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return value.replace(/\/+$/, "");
  } catch {
    return null;
  }
};

/**
 * Validates the raw settings. `abiAddress` is the address in `contracts/deployedContracts.ts`;
 * the ABI there must belong to the configured contract (section 9.7).
 */
export function parseSettings(
  raw: RawSettings,
  abiAddress: string | undefined,
): { settings: Settings; errors: string[] } {
  const errors: string[] = [];

  const privyAppId = (raw.privyAppId ?? "").trim();
  if (!privyAppId) errors.push("NEXT_PUBLIC_PRIVY_APP_ID is empty.");

  let escrowAddress: `0x${string}` = "0x0000000000000000000000000000000000000000";
  if (!raw.escrowAddress || !isAddress(raw.escrowAddress, { strict: false })) {
    errors.push("NEXT_PUBLIC_ESCROW_ADDRESS is not a valid address.");
  } else {
    escrowAddress = getAddress(raw.escrowAddress);
    if (!abiAddress || getAddress(abiAddress) !== escrowAddress) {
      errors.push("NEXT_PUBLIC_ESCROW_ADDRESS is not the address in contracts/deployedContracts.ts. Update the ABI.");
    }
  }

  const verifierUrl = httpUrl(raw.verifierUrl);
  if (!verifierUrl) errors.push("NEXT_PUBLIC_VERIFIER_URL is not an http or https URL.");

  const chainId = Number(raw.chainId);
  if (chainId !== MONAD_TESTNET_ID) errors.push(`NEXT_PUBLIC_CHAIN_ID must be ${MONAD_TESTNET_ID} (Monad testnet).`);

  const rpcUrl = httpUrl(raw.rpcUrl);
  if (!rpcUrl) errors.push("NEXT_PUBLIC_RPC_URL is not an http or https URL.");

  const explorerUrl = httpUrl(raw.explorerUrl);
  if (!explorerUrl) errors.push("NEXT_PUBLIC_EXPLORER_URL is not an http or https URL.");

  return {
    settings: {
      privyAppId,
      escrowAddress,
      verifierUrl: verifierUrl ?? "",
      chainId: MONAD_TESTNET_ID,
      rpcUrl: rpcUrl ?? "",
      explorerUrl: explorerUrl ?? "",
    },
    errors,
  };
}
