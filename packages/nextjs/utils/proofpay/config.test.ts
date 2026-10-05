import { MONAD_TESTNET_ID, parseSettings } from "./config";
import { describe, expect, it } from "vitest";

const ABI_ADDRESS = "0x0f8ae1306c78fcf7bf998f8b8ee88add1bd7a0cf";
const good = {
  privyAppId: "app-id",
  escrowAddress: ABI_ADDRESS,
  verifierUrl: "http://localhost:8000/",
  chainId: "10143",
  rpcUrl: "https://testnet-rpc.monad.xyz",
  explorerUrl: "https://testnet.monadvision.com",
};

describe("parseSettings (section 8, F-03)", () => {
  it("accepts valid settings and normalizes them", () => {
    const { settings, errors } = parseSettings(good, ABI_ADDRESS);
    expect(errors).toEqual([]);
    expect(settings.chainId).toBe(MONAD_TESTNET_ID);
    expect(settings.escrowAddress).toBe("0x0F8AE1306C78fcF7bF998f8b8eE88ADD1Bd7A0cF");
    expect(settings.verifierUrl).toBe("http://localhost:8000");
  });

  it.each(["143", "31337", "", "abc"])("rejects chain ID %s", chainId => {
    const { errors } = parseSettings({ ...good, chainId }, ABI_ADDRESS);
    expect(errors.some(e => e.startsWith("NEXT_PUBLIC_CHAIN_ID"))).toBe(true);
  });

  it("rejects missing values and bad URLs", () => {
    const { errors } = parseSettings({ chainId: "10143", verifierUrl: "ftp://x", rpcUrl: "not a url" }, ABI_ADDRESS);
    expect(errors).toHaveLength(5);
  });

  it("rejects an escrow address that is not the address of the ABI file", () => {
    const other = "0x1111111111111111111111111111111111111111";
    const { errors } = parseSettings({ ...good, escrowAddress: other }, ABI_ADDRESS);
    expect(errors).toEqual([expect.stringContaining("deployedContracts.ts")]);
  });
});
