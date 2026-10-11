/** Detect MPF in a JPEG APP2 segment (phone multi-picture JPEG), without trusting the filename. */
export function photoNeedsConversion(bytes: Uint8Array): boolean {
  if (bytes[0] === 0xff && bytes[1] === 0xd8) {
    let i = 2;
    while (i + 4 <= bytes.length && bytes[i] === 0xff) {
      const marker = bytes[i + 1];
      if (marker === 0xda || marker === 0xd9) break;
      if (marker === 0xff) {
        i++;
        continue;
      }
      const length = bytes[i + 2] * 256 + bytes[i + 3];
      if (length < 2 || i + 2 + length > bytes.length) break;
      if (
        marker === 0xe2 &&
        bytes[i + 4] === 0x4d &&
        bytes[i + 5] === 0x50 &&
        bytes[i + 6] === 0x46 &&
        bytes[i + 7] === 0
      )
        return true;
      i += 2 + length;
    }
    return false;
  }
  const png = [137, 80, 78, 71, 13, 10, 26, 10].every((b, i) => bytes[i] === b);
  const webp =
    String.fromCharCode(...bytes.slice(0, 4)) === "RIFF" && String.fromCharCode(...bytes.slice(8, 12)) === "WEBP";
  return !png && !webp;
}

/** F-13: keep supported originals; convert unsupported formats/MPO before committing the bytes. */
export async function prepareProofPhoto(file: File): Promise<{ file: Blob; converted: boolean }> {
  if (file.size > 10 * 1024 * 1024) throw new Error("The photo is too large. The limit is 10 MB.");
  if (!photoNeedsConversion(new Uint8Array(await file.arrayBuffer()))) return { file, converted: false };
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    if (
      image.naturalWidth > 8000 ||
      image.naturalHeight > 8000 ||
      image.naturalWidth * image.naturalHeight > 25_000_000
    ) {
      throw new Error("The photo is too large. Use a smaller camera photo.");
    }
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Photo conversion is unavailable in this browser.");
    context.fillStyle = "white";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0);
    const jpeg = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(b => (b ? resolve(b) : reject(new Error("Could not convert this photo."))), "image/jpeg", 0.95),
    );
    return { file: jpeg, converted: true };
  } catch {
    throw new Error("This browser cannot convert the photo. Export it as JPEG, PNG or WebP and try again.");
  } finally {
    URL.revokeObjectURL(url);
  }
}
