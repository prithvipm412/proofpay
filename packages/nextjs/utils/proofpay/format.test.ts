import { formatMon, formatTimeLeft, shortAddress } from "./format";
import { parseEther } from "viem";
import { describe, expect, it } from "vitest";

describe("shortAddress (F-04)", () => {
  it("keeps 0x + 4 characters and the last 4", () => {
    expect(shortAddress("0x12ab34cd56ef78900000000000000000aaaa9f3c")).toBe("0x12ab…9f3c");
  });
});

describe("formatMon", () => {
  it.each([
    [parseEther("0.05"), "0.05"],
    [parseEther("1"), "1"],
    [parseEther("1.23456789"), "1.2345"],
    [0n, "0"],
    [1n, "<0.0001"],
  ])("formats %s wei as %s", (wei, text) => {
    expect(formatMon(wei)).toBe(text);
  });
});

describe("formatTimeLeft (F-09)", () => {
  it.each([
    [0, null],
    [-5, null],
    [45, "45 s"],
    [600, "10 min"],
    [3600, "1 h"],
    [3600 + 12 * 60, "1 h 12 min"],
    [86400, "1 d"],
    [86400 + 3 * 3600 + 59, "1 d 3 h"],
  ])("%s seconds -> %s", (seconds, text) => {
    expect(formatTimeLeft(seconds)).toBe(text);
  });
});
