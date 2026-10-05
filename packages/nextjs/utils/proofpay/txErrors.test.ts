import { NotEnoughMonError } from "./fees";
import { describeTxError, revertErrorName } from "./txErrors";
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

  it("names the contract error", () => {
    expect(revertErrorName(reverted("AmountTooLow"))).toBe("AmountTooLow");
    expect(describeTxError(reverted("AmountTooLow"))).toMatch(/AmountTooLow/);
  });

  it("keeps plain app messages and hides others", () => {
    expect(describeTxError(new Error("Log in first."))).toBe("Log in first.");
    expect(describeTxError("x")).toBe("Something went wrong. Try again.");
  });
});
