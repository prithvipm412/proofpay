import { computeSubmitBy, parseAmount, utf8Length, validateTaskForm } from "./taskForm";
import { parseEther } from "viem";
import { describe, expect, it } from "vitest";

const ok = {
  title: "Clean bench",
  description: "Remove the litter",
  amount: "0.05",
  limitMinutes: 1440,
  hasPhoto: true,
};

describe("validateTaskForm (F-08, C-13 limits)", () => {
  it("accepts the default form", () => {
    expect(validateTaskForm(ok)).toEqual({});
  });

  it("counts bytes, not characters", () => {
    expect(utf8Length("é")).toBe(2);
    expect(validateTaskForm({ ...ok, title: "é".repeat(40) })).toEqual({});
    expect(validateTaskForm({ ...ok, title: "é".repeat(41) }).title).toMatch(/too long/);
    expect(validateTaskForm({ ...ok, description: "a".repeat(501) }).description).toMatch(/too long/);
  });

  it("rejects empty text after trimming", () => {
    const errors = validateTaskForm({ ...ok, title: "  ", description: "\n" });
    expect(Object.keys(errors).sort()).toEqual(["description", "title"]);
  });

  it.each(["0.0009", "0", "", "abc", "1e3", "-1", "0.1234567890123456789"])("rejects amount %j", amount => {
    expect(validateTaskForm({ ...ok, amount }).amount).toBeDefined();
  });

  it.each([9, 10.5, 29 * 24 * 60 + 1])("rejects time limit %s minutes", limitMinutes => {
    expect(validateTaskForm({ ...ok, limitMinutes }).limitMinutes).toBeDefined();
  });

  it("needs a photo", () => {
    expect(validateTaskForm({ ...ok, hasPhoto: false }).photo).toBeDefined();
  });
});

describe("parseAmount", () => {
  it("parses decimal MON", () => {
    expect(parseAmount("0.001")).toBe(parseEther("0.001"));
    expect(parseAmount(".5")).toBe(parseEther("0.5"));
    expect(parseAmount(" 2 ")).toBe(parseEther("2"));
  });
});

describe("computeSubmitBy", () => {
  it("adds the limit to the chain time", () => {
    expect(computeSubmitBy(1_000n, 10)).toBe(1_600n);
  });
});
