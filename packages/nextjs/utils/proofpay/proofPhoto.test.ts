import { photoNeedsConversion } from "./proofPhoto";
import { describe, expect, it } from "vitest";

describe("F-13 phone photo format detection", () => {
  it("preserves supported byte formats regardless of name/type", () => {
    expect(photoNeedsConversion(new Uint8Array([255, 216, 255, 217]))).toBe(false);
    expect(photoNeedsConversion(new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]))).toBe(false);
    expect(photoNeedsConversion(new TextEncoder().encode("RIFF1234WEBP"))).toBe(false);
  });
  it("detects a multi-picture JPEG APP2 MPF segment", () => {
    expect(photoNeedsConversion(new Uint8Array([255, 216, 255, 226, 0, 6, 77, 80, 70, 0, 255, 217]))).toBe(true);
  });
  it("does not confuse a plain APP2 segment with MPO", () => {
    expect(photoNeedsConversion(new Uint8Array([255, 216, 255, 226, 0, 6, 73, 67, 67, 0, 255, 217]))).toBe(false);
  });
  it("converts unsupported formats and tolerates truncated JPEG headers", () => {
    expect(photoNeedsConversion(new TextEncoder().encode("HEIC"))).toBe(true);
    expect(photoNeedsConversion(new Uint8Array([255, 216, 255, 226, 0, 0]))).toBe(false);
  });
});
