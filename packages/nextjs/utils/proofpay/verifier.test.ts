import { loadPreview, previewUrl, sha256Hex, uploadPhoto, verifierFetch } from "./verifier";
import { afterEach, describe, expect, it, vi } from "vitest";

const BYTES = new Uint8Array([97, 98, 99]); // "abc"
// SHA-256("abc"), a standard test vector.
const ABC_HASH = "0xba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

afterEach(() => vi.unstubAllGlobals());

describe("sha256Hex", () => {
  it("matches the standard test vector", async () => {
    expect(await sha256Hex(BYTES.buffer)).toBe(ABC_HASH);
  });
});

describe("uploadPhoto (V-09)", () => {
  const stubFetch = (response: Response | Error) =>
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        if (response instanceof Error) throw response;
        return response;
      }),
    );

  it("returns the hash when the service hash equals the local hash", async () => {
    stubFetch(new Response(JSON.stringify({ sha256: ABC_HASH.toUpperCase().replace("0X", "0x") })));
    expect(await uploadPhoto(new Blob([BYTES]), "http://v")).toBe(ABC_HASH);
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("http://v/upload");
  });

  it("refuses a different hash from the service", async () => {
    stubFetch(new Response(JSON.stringify({ sha256: "0x" + "00".repeat(32) })));
    await expect(uploadPhoto(new Blob([BYTES]), "http://v")).rejects.toThrow(/different file/);
  });

  it.each([
    [400, /JPEG, PNG or WebP/],
    [413, /10 MB/],
    [429, /Too many uploads/],
    [410, /closed/],
  ])("gives a plain message for HTTP %s", async (status, message) => {
    stubFetch(new Response(JSON.stringify({ error: "x" }), { status }));
    await expect(uploadPhoto(new Blob([BYTES]), "http://v")).rejects.toThrow(message);
  });

  it("gives a plain message when the service is not reachable", async () => {
    stubFetch(new TypeError("Failed to fetch"));
    await expect(uploadPhoto(new Blob([BYTES]), "http://v")).rejects.toThrow(/not reachable/);
  });
});

describe("previewUrl (V-10)", () => {
  it("uses the files endpoint", () => {
    expect(previewUrl(ABC_HASH, "http://v")).toBe(`http://v/files/${ABC_HASH}`);
  });
});

describe("ngrok free-plan header (M8)", () => {
  it("every verifier request sends ngrok-skip-browser-warning, also uploads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ sha256: ABC_HASH }))),
    );
    await verifierFetch("http://v/x", { headers: { "Content-Type": "application/json" } });
    await uploadPhoto(new Blob([BYTES]), "http://v");
    for (const call of vi.mocked(fetch).mock.calls) {
      const headers = (call[1] as RequestInit).headers as Record<string, string>;
      expect(headers["ngrok-skip-browser-warning"]).toBe("1");
    }
    expect(((vi.mocked(fetch).mock.calls[0][1] as RequestInit).headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
  });

  it("loads a preview once and returns an object URL", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(new Blob([BYTES]), { headers: { "content-type": "image/jpeg" } })),
    );
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:preview") });
    const hash = "0x" + "a1".repeat(32);
    expect(await loadPreview(hash, "http://once")).toBe("blob:preview");
    expect(await loadPreview(hash.toUpperCase().replace("0X", "0x"), "http://once")).toBe("blob:preview");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe(`http://once/files/${hash}`);
  });

  it("treats a warning page or a 404 as no preview, and can try again later", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>warning</html>", { headers: { "content-type": "text/html" } })),
    );
    const hash = "0x" + "b2".repeat(32);
    await expect(loadPreview(hash, "http://warn")).rejects.toThrow(/No preview/);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 404 })),
    );
    await expect(loadPreview(hash, "http://warn")).rejects.toThrow(/No preview/);
    expect(fetch).toHaveBeenCalledTimes(1); // the failed first load was not kept
  });
});
