import { createHash } from "node:crypto";

export function fingerprint(parts: string[]): string {
  return createHash("sha256").update(parts.join("|")).digest("hex").slice(0, 12);
}

export function fingerprintId(prefix: string, parts: string[]): string {
  return `${prefix}-${fingerprint(parts)}`;
}

export function fingerprintComment(id: string): string {
  return `<!-- fingerprint:${id} -->`;
}

export function fingerprintSearchToken(id: string): string {
  return `fingerprint:${id}`;
}

export function extractFingerprint(text: string): string | null {
  const match = text.match(/<!-- fingerprint:([\w:-]+) -->/);
  return match ? (match[1] ?? null) : null;
}
