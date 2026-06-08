export function int16ToBase64(pcmData: Int16Array): string {
  const bytes = new Uint8Array(pcmData.buffer, pcmData.byteOffset, pcmData.byteLength);
  // Process in 8KB blocks to avoid call stack overflow with String.fromCharCode
  const chunks: string[] = [];
  const BLOCK = 8192;
  for (let i = 0; i < bytes.length; i += BLOCK) {
    const slice = bytes.subarray(i, Math.min(i + BLOCK, bytes.length));
    chunks.push(String.fromCharCode(...slice));
  }
  return btoa(chunks.join(""));
}
