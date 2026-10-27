/**
 * WasmHost for the GSC Live Preview iframe shell.
 * The shell owns the Emscripten Module, MEMFS external slot and all
 * control/script/state bridging; React only posts messages and listens.
 */

import type { PreviewParams } from './gsc'
import { EXTERNAL_DAT_NAME, EXTERNAL_RAW_NAME } from './datasets'

export type WasmHost = {
  reset: () => void
  runScript: (source: string) => Promise<number>
  applyControls: (params: PreviewParams) => void
  restoreDataset: (index: number) => void
  loadExternalVolume: (datUrl: string, rawUrl: string) => Promise<number>
  setCustomTf: (encoding: string) => void
  captureScreenshot: () => Promise<Blob>
  dispose: () => void
}

export type WasmStatePatch = Partial<PreviewParams> & { message?: string }

export const WASM_ASSET_PATH = '/wasm/sampling.js'
const RUN_TIMEOUT_MS = 120_000
const LOAD_TIMEOUT_MS = 180_000

export class WasmEngineMissingError extends Error {
  constructor() {
    super('Live Preview engine is missing. Run npm run wasm:build from frontend/ and reload.')
    this.name = 'WasmEngineMissingError'
  }
}

export async function assertWasmAssets(signal?: AbortSignal): Promise<void> {
  // NOTE: the Python harness only implements GET (HEAD → 501), so probe with
  // GET. sampling.js is ~132KB; the content-type check rejects HTML fallbacks
  // without trusting status alone.
  const response = await fetch(WASM_ASSET_PATH, { method: 'GET', signal, cache: 'no-store' })
  const contentType = response.headers.get('content-type') ?? ''
  if (!response.ok || /text\/html/i.test(contentType)) {
    throw new WasmEngineMissingError()
  }
  // Drain the body so the connection can be reused.
  try {
    await response.arrayBuffer()
  } catch {
    /* aborted — headers already proved the asset exists */
  }
}

/**
 * Preview shell URL.
 *
 * Prefer a cross-site twin host (127.0.0.1 ↔ localhost) so Chromium keeps the
 * Raycaster iframe in another renderer process. A same-origin iframe shares
 * this document's main thread: the ~100MB MEMFS unpack, the Wasm compile and
 * every synchronous control/script ccall then freeze React and its BusyRegion.
 * Both loopback names resolve to the same server (Vite binds 127.0.0.1 and
 * accepts the twin Host; the Python harness accepts both Host headers), so the
 * engine moves off the UI process without any extra listener.
 */
export function wasmShellUrl(): string {
  const shellPath = '/wasm/preview-shell.html'
  if (typeof window === 'undefined') return shellPath
  const { protocol, hostname, port } = window.location
  const twin =
    hostname === '127.0.0.1' ? 'localhost' : hostname === 'localhost' ? '127.0.0.1' : null
  if (!twin) return shellPath
  return `${protocol}//${twin}${port ? `:${port}` : ''}${shellPath}`
}

function postToIframe(iframe: HTMLIFrameElement, message: unknown, transfer?: Transferable[]): void {
  const target = iframe.contentWindow
  if (!target) return
  if (transfer && transfer.length > 0) {
    target.postMessage(message, '*', transfer)
  } else {
    target.postMessage(message, '*')
  }
}

export function createWasmHost(
  iframe: HTMLIFrameElement,
  options?: {
    signal?: AbortSignal
    onProgress?: (label: string, value?: number | null) => void
    onState?: (patch: WasmStatePatch) => void
    onMessage?: (text: string) => void
    onContextLost?: () => void
    onContextRestored?: () => void
  },
): Promise<WasmHost> {
  const signal = options?.signal
  const onProgress = options?.onProgress
  const onState = options?.onState
  const onMessage = options?.onMessage
  const onContextLost = options?.onContextLost
  const onContextRestored = options?.onContextRestored

  return assertWasmAssets(signal).then(
    () =>
      new Promise((resolve, reject) => {
        if (signal?.aborted) {
          reject(new DOMException('Aborted', 'AbortError'))
          return
        }
        let settled = false
        const timeout = window.setTimeout(() => {
          cleanup()
          reject(new Error('Preview took too long to load. Run npm run wasm:build and retry.'))
        }, LOAD_TIMEOUT_MS)

        const onAbort = () => {
          cleanup()
          reject(new DOMException('Aborted', 'AbortError'))
        }

        const pendingRuns = new Map<number, { resolve: (code: number) => void; reject: (e: Error) => void; timer: number }>()
        let runSeq = 0
        const pendingShots = new Map<number, { resolve: (blob: Blob) => void; reject: (e: Error) => void; timer: number }>()
        let shotSeq = 0

        const cleanup = () => {
          window.clearTimeout(timeout)
          window.removeEventListener('message', onMessageHandler)
          signal?.removeEventListener('abort', onAbort)
          for (const [, pending] of pendingRuns) {
            window.clearTimeout(pending.timer)
            pending.reject(new Error('Preview host disposed'))
          }
          pendingRuns.clear()
          for (const [, pending] of pendingShots) {
            window.clearTimeout(pending.timer)
            pending.reject(new Error('Preview host disposed'))
          }
          pendingShots.clear()
        }

        const onMessageHandler = (event: MessageEvent) => {
          if (event.source !== iframe.contentWindow) return
          const data = event.data as Record<string, unknown>
          if (!data || typeof data !== 'object') return
          const type = data['type']
          if (type === 'wasmProgress' && typeof data['text'] === 'string') {
            onProgress?.(data['text'] as string, null)
            return
          }
          if (type === 'wasmReady') {
            if (settled) return
            settled = true
            window.clearTimeout(timeout)
            // Keep the message listener for run/state traffic.
            resolve({
              reset: () => postToIframe(iframe, { type: 'reset' }),
              runScript: (source: string) =>
                new Promise<number>((res, rej) => {
                  const id = ++runSeq
                  const timer = window.setTimeout(() => {
                    pendingRuns.delete(id)
                    rej(new Error(`Preview run timed out (${RUN_TIMEOUT_MS / 1000}s)`))
                  }, RUN_TIMEOUT_MS)
                  pendingRuns.set(id, { resolve: res, reject: rej, timer })
                  postToIframe(iframe, { type: 'runScript', id, source })
                }),
              applyControls: (params: PreviewParams) =>
                postToIframe(iframe, {
                  type: 'applyControls',
                  controls: {
                    dataset: params.dataset,
                    method: params.method,
                    constant: params.constantSampleCount,
                    ortho: params.ortho,
                    virtualSamples: params.virtualSamples,
                    samplingRateExp: params.samplingRateExp,
                    tfType: params.tfType,
                    tfStart: params.tfStart,
                    tfWidth: params.tfWidth,
                    customTf: params.customTf,
                    alpha: params.ertEnabled ? 0.99 : 2.0,
                    level: params.level,
                    transform: params.transform,
                  },
                }),
              restoreDataset: (index: number) =>
                postToIframe(iframe, {
                  type: 'applyControls',
                  controls: { dataset: index },
                }),
              loadExternalVolume: async (datUrl: string, rawUrl: string) => {
                const [datResp, rawResp] = await Promise.all([fetch(datUrl), fetch(rawUrl)])
                if (!datResp.ok) throw new Error(`Cannot read preview .dat (${datResp.status})`)
                if (!rawResp.ok) throw new Error(`Cannot read preview .raw (${rawResp.status})`)
                let datText = await datResp.text()
                const rawBuffer = await rawResp.arrayBuffer()
                // Keep MEMFS slot stable: rewrite ObjectFileName to the raw slot name.
                const rawBase = EXTERNAL_RAW_NAME.replace(/^\//, '')
                datText = datText.replace(/ObjectFileName\s*:\s*\S+/i, `ObjectFileName: ${rawBase}`)
                const rawCopy = rawBuffer.slice(0)
                return new Promise<number>((res, rej) => {
                  const id = ++runSeq
                  const timer = window.setTimeout(() => {
                    pendingRuns.delete(id)
                    rej(new Error('External volume load timed out'))
                  }, RUN_TIMEOUT_MS)
                  pendingRuns.set(id, { resolve: res, reject: rej, timer })
                  postToIframe(
                    iframe,
                    { type: 'loadExternal', id, datName: EXTERNAL_DAT_NAME, datText, rawName: EXTERNAL_RAW_NAME },
                    [],
                  )
                  // Raw bytes follow in a second message to avoid cloning limits on some browsers.
                  // The shell buffers by id; order is preserved for same-target postMessage.
                  postToIframe(iframe, { type: 'loadExternalBytes', id, rawName: EXTERNAL_RAW_NAME, bytes: rawCopy }, [
                    rawCopy,
                  ])
                })
              },
              setCustomTf: (encoding: string) => postToIframe(iframe, { type: 'setCustomTf', encoding }),
              captureScreenshot: () =>
                new Promise<Blob>((res, rej) => {
                  const id = ++shotSeq
                  const timer = window.setTimeout(() => {
                    pendingShots.delete(id)
                    rej(new Error('Screenshot timed out'))
                  }, 15000)
                  pendingShots.set(id, { resolve: res, reject: rej, timer })
                  postToIframe(iframe, { type: 'captureScreenshot', id })
                }),
              dispose: () => {
                cleanup()
              },
            })
            return
          }
          if (type === 'runResult') {
            const id = typeof data['id'] === 'number' ? (data['id'] as number) : 0
            const pending = id ? pendingRuns.get(id) : [...pendingRuns.values()][0] ? [...pendingRuns.entries()][0]?.[1] : undefined
            const key = id || [...pendingRuns.keys()][0]
            if (key != null && pending) {
              pendingRuns.delete(key)
              window.clearTimeout(pending.timer)
              if (typeof data['error'] === 'string' && (data['error'] as string)) {
                pending.reject(new Error(data['error'] as string))
              } else {
                pending.resolve(typeof data['code'] === 'number' ? (data['code'] as number) : -1)
              }
            }
            return
          }
          if (type === 'wasmErr' && typeof data['text'] === 'string') {
            onMessage?.(data['text'] as string)
            return
          }
          if (type === 'wasmMessage' && typeof data['text'] === 'string') {
            onMessage?.(data['text'] as string)
            return
          }
          // GPU context lifecycle: the shell preventDefaults the loss so the
          // context can be restored; the parent then re-pushes the controls.
          if (type === 'wasmContextLost') {
            onContextLost?.()
            return
          }
          if (type === 'wasmContextRestored') {
            onContextRestored?.()
            return
          }
          // Granular state readback from C++ callbacks.
          if (type === 'wasmState' && data['patch'] && typeof data['patch'] === 'object') {
            onState?.(data['patch'] as WasmStatePatch)
            return
          }
          if (type === 'screenshotResult') {
            const id = typeof data['id'] === 'number' ? (data['id'] as number) : 0
            const pending = id ? pendingShots.get(id) : undefined
            if (id != null && pending) {
              pendingShots.delete(id)
              window.clearTimeout(pending.timer)
              if (typeof data['error'] === 'string' && (data['error'] as string)) {
                pending.reject(new Error(data['error'] as string))
              } else if (data['bytes'] instanceof ArrayBuffer) {
                const mime = typeof data['mime'] === 'string' ? (data['mime'] as string) : 'image/png'
                pending.resolve(new Blob([data['bytes'] as ArrayBuffer], { type: mime }))
              } else if (typeof data['dataUrl'] === 'string') {
                fetch(data['dataUrl'] as string)
                  .then((r) => r.blob())
                  .then(pending.resolve, pending.reject)
              } else {
                pending.reject(new Error('Screenshot produced no image'))
              }
            }
            return
          }
        }

        signal?.addEventListener('abort', onAbort)
        window.addEventListener('message', onMessageHandler)
        onProgress?.('Loading Raycaster', null)
        iframe.src = wasmShellUrl()
      }),
  )
}
