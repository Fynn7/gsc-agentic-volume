/**
 * Custom transfer-function codec matching VirtualSampling C++:
 * planar [R,G,B,A] uint8 (n=256) → RLE ('RLEF' + u64LE size + runs) → Base64Url no-pad.
 */

const RLE_MAGIC = [0x52, 0x4c, 0x45, 0x46] // 'RLEF'
const B64_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'

function b64Value(c: string): number {
  const code = c.charCodeAt(0)
  if (code >= 65 && code <= 90) return code - 65
  if (code >= 97 && code <= 122) return code - 97 + 26
  if (code >= 48 && code <= 57) return code - 48 + 52
  if (c === '-') return 62
  if (c === '_') return 63
  return -1
}

export function base64UrlEncodeNoPad(data: Uint8Array): string {
  let out = ''
  let i = 0
  while (i + 3 <= data.length) {
    const v = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
    out += B64_ALPHABET[(v >> 18) & 63] + B64_ALPHABET[(v >> 12) & 63] + B64_ALPHABET[(v >> 6) & 63] + B64_ALPHABET[v & 63]
    i += 3
  }
  const rem = data.length - i
  if (rem === 1) {
    const v = data[i] << 16
    out += B64_ALPHABET[(v >> 18) & 63] + B64_ALPHABET[(v >> 12) & 63]
  } else if (rem === 2) {
    const v = (data[i] << 16) | (data[i + 1] << 8)
    out += B64_ALPHABET[(v >> 18) & 63] + B64_ALPHABET[(v >> 12) & 63] + B64_ALPHABET[(v >> 6) & 63]
  }
  return out
}

export function base64UrlDecodeNoPad(s: string): Uint8Array {
  const out: number[] = []
  let buf = 0
  let bits = 0
  for (const c of s) {
    const v = b64Value(c)
    if (v < 0) throw new Error('Invalid Base64Url character')
    buf = (buf << 6) | v
    bits += 6
    if (bits >= 8) {
      bits -= 8
      out.push((buf >> bits) & 0xff)
    }
  }
  if (bits > 0) {
    const mask = (1 << bits) - 1
    if ((buf & mask) !== 0) throw new Error('Invalid Base64Url tail bits')
  }
  return new Uint8Array(out)
}

export function rleCompress(input: Uint8Array): Uint8Array {
  const n = input.length
  const out: number[] = [...RLE_MAGIC]
  // u64LE original size
  let size = n
  for (let i = 0; i < 8; i++) {
    out.push(size & 0xff)
    size = Math.floor(size / 256)
  }
  let i = 0
  while (i < n) {
    const v = input[i]
    let run = 1
    while (i + run < n && input[i + run] === v && run < 255) run++
    out.push(run, v)
    i += run
  }
  return new Uint8Array(out)
}

export function rleDecompress(input: Uint8Array): Uint8Array {
  if (input.length < 12) throw new Error('RLE payload too small')
  if (input[0] !== 0x52 || input[1] !== 0x4c || input[2] !== 0x45 || input[3] !== 0x46) {
    throw new Error('Bad RLE magic')
  }
  let origSize = 0
  let mult = 1
  for (let i = 0; i < 8; i++) {
    origSize += input[4 + i] * mult
    mult *= 256
  }
  const out: number[] = []
  let p = 12
  while (p < input.length) {
    if (p + 2 > input.length) throw new Error('Truncated RLE run')
    const count = input[p++]
    const value = input[p++]
    if (count === 0) throw new Error('Invalid RLE run length')
    if (out.length + count > origSize) throw new Error('RLE output overflow')
    for (let k = 0; k < count; k++) out.push(value)
  }
  if (out.length !== origSize) throw new Error('RLE size mismatch')
  return new Uint8Array(out)
}

export type TfChannels = { r: number[]; g: number[]; b: number[]; a: number[] }

/** Decode a Custom TF URL payload into 4×n float channels. */
export function decodeCustomTf(encoded: string): TfChannels {
  const packed = base64UrlDecodeNoPad(encoded)
  const bytes = rleDecompress(packed)
  if (bytes.length % 4 !== 0) throw new Error('TF payload not divisible by 4')
  const n = bytes.length / 4
  const r: number[] = []
  const g: number[] = []
  const b: number[] = []
  const a: number[] = []
  for (let i = 0; i < n; i++) {
    r.push(bytes[i] / 255)
    g.push(bytes[n + i] / 255)
    b.push(bytes[2 * n + i] / 255)
    a.push(bytes[3 * n + i] / 255)
  }
  return { r, g, b, a }
}

/** Encode 4 planar float channels into the Custom TF URL payload. */
export function encodeCustomTf(ch: TfChannels): string {
  const n = ch.r.length
  if (ch.g.length !== n || ch.b.length !== n || ch.a.length !== n || n === 0) {
    throw new Error('TF channels must share a non-empty length')
  }
  const bytes = new Uint8Array(n * 4)
  const toByte = (v: number) => Math.max(0, Math.min(255, Math.round(v * 255)))
  for (let i = 0; i < n; i++) {
    bytes[i] = toByte(ch.r[i])
    bytes[n + i] = toByte(ch.g[i])
    bytes[2 * n + i] = toByte(ch.b[i])
    bytes[3 * n + i] = toByte(ch.a[i])
  }
  return base64UrlEncodeNoPad(rleCompress(bytes))
}

/** Smoothstep preset for one channel (matches C++ TransferFunction::smoothStep). */
export function smoothstepChannel(n: number, start: number, width: number): number[] {
  const out: number[] = []
  const w = Math.max(1e-5, width)
  for (let i = 0; i < n; i++) {
    const x = n > 1 ? i / (n - 1) : 0
    let t = (x - start) / w
    t = Math.min(1, Math.max(0, t))
    t = t * t * (3 - 2 * t)
    out.push(t)
  }
  return out
}

export function defaultCustomChannels(n = 256): TfChannels {
  const base = smoothstepChannel(n, 0.1, 0.12)
  return { r: [...base], g: [...base], b: [...base], a: [...base] }
}
