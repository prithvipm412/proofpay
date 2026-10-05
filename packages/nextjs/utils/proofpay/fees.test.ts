import { gasLimitFor, maxCost } from "./fees";
import { describe, expect, it } from "vitest";

describe("gas limit and cost (F-05)", () => {
  it("adds 25 % to the estimate and rounds up", () => {
    expect(gasLimitFor(200_000n)).toBe(250_000n);
    expect(gasLimitFor(3n)).toBe(4n);
  });

  it("the cost is the value plus limit x max fee", () => {
    expect(maxCost(10n, 100n, 2n)).toBe(210n);
  });
});
