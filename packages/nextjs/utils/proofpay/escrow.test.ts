import { newestIds } from "./escrow";
import { describe, expect, it } from "vitest";

describe("newestIds (F-09 board window)", () => {
  it("lists from newest to oldest and never includes 0", () => {
    expect(newestIds(3n, 100n)).toEqual([3n, 2n, 1n]);
    expect(newestIds(0n, 100n)).toEqual([]);
  });

  it("keeps only the window", () => {
    expect(newestIds(250n, 3n)).toEqual([250n, 249n, 248n]);
  });
});
