import { NotEnoughMonError } from "./fees";
import { CONTRACT_ERRORS, describeTxError, revertErrorName } from "./txErrors";
import { ContractFunctionRevertedError, InsufficientFundsError, UserRejectedRequestError } from "viem";
import { describe, expect, it } from "vitest";
import { escrowAbi } from "~~/utils/proofpay/escrow";

const reverted = (errorName: string) => {
  // Selector of the custom error, so that viem can decode it from the ABI.
  const item = escrowAbi.find(i => i.type === "error" && i.name === errorName);
  if (!item) throw new Error(`no error ${errorName} in ABI`);
  const err = new ContractFunctionRevertedError({ abi: escrowAbi, functionName: "createTask", message: "" });
  Object.assign(err, { data: { errorName, abiItem: item, args: [] } });
  return err;
};

describe("describeTxError", () => {
  it("gives the F-05 sentence for missing MON", () => {
    expect(describeTxError(new NotEnoughMonError())).toBe("You need a little test MON to do this.");
    expect(describeTxError(new InsufficientFundsError())).toBe("You need a little test MON to do this.");
  });

  it("detects a cancel in the wallet", () => {
    expect(describeTxError(new UserRejectedRequestError(new Error("no")))).toBe("You cancelled this action.");
  });

  it("F-18 translates the contract error into a sentence", () => {
    expect(revertErrorName(reverted("AmountTooLow"))).toBe("AmountTooLow");
    expect(describeTxError(reverted("AmountTooLow"))).toBe("The payment must be at least 0.001 MON.");
  });

  it("F-18 covers every error in the deployed ABI without technical main-path words", () => {
    for (const item of escrowAbi) {
      if (item.type !== "error") continue;
      expect(CONTRACT_ERRORS[item.name], item.name).toBeTruthy();
      expect(describeTxError(reverted(item.name))).not.toMatch(/transaction|gas|hash|0x/i);
    }
  });

  it("keeps plain app messages and hides others", () => {
    expect(describeTxError(new Error("Log in first."))).toBe("Log in first.");
    expect(describeTxError("x")).toBe("Something went wrong. Try again.");
  });
});
