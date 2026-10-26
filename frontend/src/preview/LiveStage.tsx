import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  CheckIcon,
  CodeBlock,
  Collapsible,
  ControlBlock,
  ControlRow,
  DropdownMenu,
  DropdownMenuItem,
  DownloadIcon,
  EmptyState,
  FieldBlock,
  FieldStack,
  IconButton,
  InfoIcon,
  Slider,
  Surface,
  Tooltip,
  snackbar,
} from '@fynns/ui'
import { Code, Rocket } from 'lucide-react'
import { importVolumeFile, pickVolumeFile, type VolumeImport } from '../api'
import { GscReferenceDialog } from './gscReference'
import { GscEditor } from './GscEditor'
import { GSC_LANGUAGE } from './gscHighlight'
import { formatValidationSummary, validateGscSource, type GscValidationIssue } from './gscValidate'
import { BUILTIN_DATASETS, datasetLabel } from './datasets'
import { RENDER_METHODS, SAMPLING_RATE_EXPS, formatSamplingRate, isVirtualMethod } from './methods'
import { CustomTfEditor } from './CustomTfEditor'
import {
  GSC_DATASET_KEY,
  readStoredDraft,
  readStoredParams,
  storeDraft,
  storeParams,
  syncParamsToScript,
  type PreviewParams,
} from './gsc'
import { createWasmHost, WasmEngineMissingError, type WasmHost, type WasmStatePatch } from './wasmHost'

type LiveStageProps = {
  source: string
  previewImport: VolumeImport | null
  onPreviewImport: (value: VolumeImport | null) => void
  onSourceChange: (path: string) => void
}

/** Window event fired by the top bar to download .gsc + preview PNG. */
export const STAGE_EXPORT_EVENT = 'gav:export-stage'

function persistParams(p: PreviewParams): void {
  storeParams(p)
  try {
    localStorage.setItem(GSC_DATASET_KEY, String(p.dataset))
  } catch {
    /* ignore */
  }
}

function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = name
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 4000)
  }
}

function stageStamp(params: PreviewParams): { stamp: string; base: string } {
  const stamp = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const stampText =
    `${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}-` +
    `${pad(stamp.getHours())}${pad(stamp.getMinutes())}${pad(stamp.getSeconds())}`
  return { stamp: stampText, base: `gsc-preview-d${params.dataset}-m${params.method}-${stampText}` }
}

function clampInt(v: number, lo: number, hi: number, fallback: number): number {
  if (!Number.isFinite(v)) return fallback
  return Math.min(hi, Math.max(lo, Math.round(v)))
}

/** Default scratch diagnostic while editor / Wasm / run settle (mirrors gsc-live-preview). */
function formatStartupDiagnostic(ready: boolean, wasm: boolean, busy: boolean): string {
  if (!ready && !wasm) return 'Preparing workspace…'
  if (!ready) return 'Loading editor…'
  if (!wasm) return 'Loading Raycaster…'
  if (busy) return 'Rendering preview…'
  return 'Standby'
}

export function LiveStage({ source, previewImport, onPreviewImport, onSourceChange }: LiveStageProps) {
  const [params, setParams] = useState<PreviewParams>(() => {
    const stored = readStoredParams()
    try {
      const raw = localStorage.getItem(GSC_DATASET_KEY)
      if (raw != null) {
        const idx = Number.parseInt(raw, 10)
        if (Number.isFinite(idx)) stored.dataset = idx
      }
    } catch {
      /* ignore */
    }
    return stored
  })
  const [gscText, setGscText] = useState<string>(() => readStoredDraft())
  const [editorReady, setEditorReady] = useState(false)
  const [wasmReady, setWasmReady] = useState(false)
  const [wasmMissing, setWasmMissing] = useState(false)
  const [runBusy, setRunBusy] = useState(false)
  const [diagnostics, setDiagnostics] = useState<{ message: string; isError: boolean }>({
    message: 'Standby',
    isError: false,
  })
  const [importing, setImporting] = useState(false)
  const [snapshotBusy, setSnapshotBusy] = useState(false)
  const [externalIndex, setExternalIndex] = useState<number | null>(null)
  const [referenceOpen, setReferenceOpen] = useState(false)

  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const hostRef = useRef<WasmHost | null>(null)
  const paramsRef = useRef(params)
  const gscRef = useRef(gscText)
  const previewImportRef = useRef(previewImport)
  const runBusyRef = useRef(runBusy)
  const completedRunRef = useRef(false)
  const editorIssuesRef = useRef<GscValidationIssue[]>([])
  paramsRef.current = params
  gscRef.current = gscText
  previewImportRef.current = previewImport
  runBusyRef.current = runBusy

  const handleEditorReady = useCallback((ready: boolean) => {
    setEditorReady(ready)
  }, [])

  const handleEditorValidation = useCallback(
    (issues: GscValidationIssue[]) => {
      editorIssuesRef.current = issues
      if (issues.length > 0) {
        setDiagnostics({ message: formatValidationSummary(issues), isError: true })
        return
      }
      // Validation cleared: keep an in-flight run's status, keep a completed
      // run's result, else fall back to the startup copy (Standby once editor
      // + Wasm are both ready). Without the completed-run guard, the settle
      // rewrite after a run would demote "Ran …" back to "Standby".
      if (runBusyRef.current || completedRunRef.current) return
      setDiagnostics({ message: formatStartupDiagnostic(editorReady, wasmReady, false), isError: false })
    },
    [editorReady, wasmReady],
  )

  // Surface boot copy (Preparing / Loading … / Standby) while editor + Wasm settle.
  useEffect(() => {
    if (editorIssuesRef.current.length > 0 || runBusyRef.current) return
    setDiagnostics({ message: formatStartupDiagnostic(editorReady, wasmReady, false), isError: false })
  }, [editorReady, wasmReady])

  // Autosave the editor text (params persist on the settle path below, not per tick).
  useEffect(() => {
    storeDraft(gscText)
  }, [gscText])

  // Preview push policy: every committed params change is pushed to Wasm
  // immediately — the original live behavior: the canvas follows the hand
  // tick-by-tick while dragging. Updaters stay pure; the push lives in this
  // effect so StrictMode double-invoked updaters can never double-send.
  // (.gsc text + params persistence keep the settle rhythm below.)
  //
  // Echo cancellation: every push is recorded in a short history. C++ emits
  // full-state bursts that can arrive AFTER newer state was pushed (script
  // batches run over frames; messages queue behind renders). Without a check,
  // a stale burst re-commits old values, the effect re-pushes them, C++
  // re-reports — a self-sustaining ping-pong that shakes the UI forever.
  // An echo key that matches ANY recently sent snapshot is a delayed
  // confirmation, never news, and is dropped before it can clobber.
  const recentSentRef = useRef<PreviewParams[]>([])
  const noteSent = useCallback((snapshot: PreviewParams) => {
    const history = recentSentRef.current
    history.push(snapshot)
    if (history.length > 8) history.splice(0, history.length - 8)
  }, [])
  const wasRecentlySent = useCallback((key: keyof PreviewParams, value: PreviewParams[keyof PreviewParams]): boolean => {
    const history = recentSentRef.current
    for (let i = history.length - 1; i >= 0; i--) {
      const sent = history[i][key]
      if (typeof sent === 'number' && typeof value === 'number') {
        if (Math.abs(sent - value) < 1e-6) return true
      } else if (Object.is(sent, value)) {
        return true
      }
    }
    return false
  }, [])
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    host.applyControls(params)
    noteSent(params)
  }, [params, noteSent])

  // Single rAF mailbox for ALL params traffic (GUI gestures + Wasm echoes).
  // Every input event / echo only records the latest value per key; at most
  // one setState commits per animation frame. The committed snapshot is never
  // older than one frame, so the slider thumb can't snap back to stale values
  // (the infinite-jitter pattern) no matter how slow renders get. Sends stay
  // live: the preview effect pushes every committed snapshot to Wasm.
  const paramsMailboxRef = useRef<Partial<PreviewParams> | null>(null)
  const paramsFlushRafRef = useRef<number | null>(null)
  const flushParamsMailbox = useCallback(() => {
    paramsFlushRafRef.current = null
    const merged = paramsMailboxRef.current
    paramsMailboxRef.current = null
    if (!merged) return
    // TEMP-DEBUG: trace commits (removed before ship).
    try {
      console.debug(`[mbox] flush keys=${Object.keys(merged).join(',')}`)
    } catch { /* ignore */ }
    setParams((prev) => {
      // Bail out when the merged snapshot adds nothing: no render, and the
      // preview effect below stays quiet (echo convergence fast-path).
      for (const key of Object.keys(merged) as (keyof PreviewParams)[]) {
        if (!Object.is(merged[key], prev[key])) return { ...prev, ...merged }
      }
      return prev
    })
  }, [])
  const queueParamsPatch = useCallback(
    (patch: Partial<PreviewParams>) => {
      paramsMailboxRef.current = { ...paramsMailboxRef.current, ...patch }
      if (paramsFlushRafRef.current == null) {
        paramsFlushRafRef.current = requestAnimationFrame(flushParamsMailbox)
      }
    },
    [flushParamsMailbox],
  )
  // Drop any pending flush on unmount.
  useEffect(
    () => () => {
      if (paramsFlushRafRef.current != null) {
        cancelAnimationFrame(paramsFlushRafRef.current)
        paramsFlushRafRef.current = null
      }
      paramsMailboxRef.current = null
    },
    [],
  )

  // Drain the mailbox synchronously and return the fresh snapshot. Used by
  // Run/export so they never act on a snapshot that is a frame stale.
  const drainMailboxSnapshot = useCallback((): PreviewParams => {
    const pending = paramsMailboxRef.current
    paramsMailboxRef.current = null
    if (paramsFlushRafRef.current != null) {
      cancelAnimationFrame(paramsFlushRafRef.current)
      paramsFlushRafRef.current = null
    }
    if (!pending) return paramsRef.current
    const snap: PreviewParams = { ...paramsRef.current, ...pending }
    // TEMP-DEBUG: trace drain (removed before ship).
    try {
      console.debug(`[mbox] drain keys=${Object.keys(pending).join(',')}`)
    } catch { /* ignore */ }
    setParams((prev) => {
      for (const key of Object.keys(pending) as (keyof PreviewParams)[]) {
        if (!Object.is(pending[key], prev[key])) return { ...prev, ...pending }
      }
      return prev
    })
    return snap
  }, [])

  // Settle path: persistence + .gsc generated-zone rewrite wait for one whole
  // GUI operation to finish — no per-tick localStorage churn or editor churn.
  // No manual sync UI; Run/export flush first so they see the settled state.
  const GSC_SYNC_DEBOUNCE_MS = 500
  const gscSyncTimer = useRef<number | null>(null)
  const flushGscSync = useCallback((): PreviewParams => {
    if (gscSyncTimer.current != null) {
      window.clearTimeout(gscSyncTimer.current)
      gscSyncTimer.current = null
    }
    // Drain first: the mailbox may hold a snapshot newer than the last commit.
    const snap = drainMailboxSnapshot()
    persistParams(snap)
    const next = syncParamsToScript(gscRef.current, snap)
    if (next !== gscRef.current) setGscText(next)
    return snap
  }, [drainMailboxSnapshot])

  useEffect(() => {
    if (gscSyncTimer.current != null) window.clearTimeout(gscSyncTimer.current)
    gscSyncTimer.current = window.setTimeout(() => {
      gscSyncTimer.current = null
      persistParams(paramsRef.current)
      const next = syncParamsToScript(gscRef.current, paramsRef.current)
      if (next !== gscRef.current) setGscText(next)
    }, GSC_SYNC_DEBOUNCE_MS)
    return () => {
      if (gscSyncTimer.current != null) {
        window.clearTimeout(gscSyncTimer.current)
        gscSyncTimer.current = null
      }
    }
  }, [params])

  const applyPatch = useCallback(
    (patch: WasmStatePatch) => {
      // Boot gate: until the first post-boot push is sent, shell echoes only
      // carry stale startup defaults (dataset 0, TF 0.1/0.12, ...) that must not
      // clobber the panel — especially stored params or the pre-reboot state.
      // The single exception is external slot discovery, which params may lack.
      if (!bootPushedRef.current) {
        const discovered = patch.dataset
        if (typeof discovered === 'number' && Number.isFinite(discovered) && discovered >= BUILTIN_DATASETS.length) {
          setExternalIndex(discovered)
          queueParamsPatch({ dataset: discovered })
        }
        return
      }
      // Validate against the last committed snapshot and queue only real
      // changes; the rAF flush merges everything into one setState.
      // Echo cancellation: a value that matches ANY recently sent snapshot is
      // a delayed confirmation of our own push, never news — dropping it here
      // breaks the parent<->engine ping-pong that otherwise shakes the UI.
      // (UI input bypasses this entirely in updateParams and always commits.)
      const base = paramsRef.current
      const fresh = <K extends keyof PreviewParams>(key: K, value: PreviewParams[K]): boolean => {
        if (Object.is(value, base[key])) return false
        if (wasRecentlySent(key, value)) return false
        return true
      }
      const clean: Partial<PreviewParams> = {}
      if (typeof patch.dataset === 'number' && Number.isFinite(patch.dataset)) {
        const v = clampInt(patch.dataset, 0, 64, base.dataset)
        if (v !== base.dataset && !wasRecentlySent('dataset', v)) clean.dataset = v
      }
      if (typeof patch.method === 'number' && Number.isFinite(patch.method)) {
        const v = clampInt(patch.method, 0, RENDER_METHODS.length - 1, base.method)
        if (v !== base.method && !wasRecentlySent('method', v)) clean.method = v
      }
      if (typeof patch.samplingRateExp === 'number' && Number.isFinite(patch.samplingRateExp)) {
        const v = clampInt(patch.samplingRateExp, -5, 6, base.samplingRateExp)
        if (v !== base.samplingRateExp && !wasRecentlySent('samplingRateExp', v)) clean.samplingRateExp = v
      }
      if (typeof patch.virtualSamples === 'number' && Number.isFinite(patch.virtualSamples)) {
        const v = clampInt(patch.virtualSamples, 1, 50, base.virtualSamples)
        if (v !== base.virtualSamples && !wasRecentlySent('virtualSamples', v)) clean.virtualSamples = v
      }
      if (typeof patch.constantSampleCount === 'boolean' && fresh('constantSampleCount', patch.constantSampleCount)) {
        clean.constantSampleCount = patch.constantSampleCount
      }
      if (typeof patch.ortho === 'boolean' && fresh('ortho', patch.ortho)) {
        clean.ortho = patch.ortho
      }
      if (typeof patch.ertEnabled === 'boolean' && fresh('ertEnabled', patch.ertEnabled)) {
        clean.ertEnabled = patch.ertEnabled
      }
      if (typeof patch.tfType === 'number' && (patch.tfType === 0 || patch.tfType === 1 || patch.tfType === 2)) {
        if (patch.tfType !== base.tfType && !wasRecentlySent('tfType', patch.tfType)) clean.tfType = patch.tfType
      }
      if (typeof patch.tfStart === 'number' && Number.isFinite(patch.tfStart)) {
        const v = Math.min(1, Math.max(0, patch.tfStart))
        if (Math.abs(v - base.tfStart) > 1e-6 && !wasRecentlySent('tfStart', v)) clean.tfStart = v
      }
      if (typeof patch.tfWidth === 'number' && Number.isFinite(patch.tfWidth)) {
        const v = Math.min(1, Math.max(0.00001, patch.tfWidth))
        if (Math.abs(v - base.tfWidth) > 1e-6 && !wasRecentlySent('tfWidth', v)) clean.tfWidth = v
      }
      if (typeof patch.customTf === 'string' && patch.customTf !== base.customTf && !wasRecentlySent('customTf', patch.customTf)) {
        clean.customTf = patch.customTf
      }
      if (typeof patch.transform === 'string' && patch.transform !== base.transform && !wasRecentlySent('transform', patch.transform)) {
        clean.transform = patch.transform
      }
      if (typeof patch.level === 'number' && Number.isFinite(patch.level)) {
        const v = clampInt(patch.level, 0, 8, base.level)
        if (v !== base.level && !wasRecentlySent('level', v)) clean.level = v
      }
      if (Object.keys(clean).length > 0) queueParamsPatch(clean)
    },
    [queueParamsPatch, wasRecentlySent],
  )

  const ensureExternalLoaded = useCallback(
    async (host: WasmHost, record: VolumeImport, wantDataset: number) => {
      // Reuse the single MEMFS slot; the C++ side appends one entry per load
      // and reports the new index via wasmState.volume.
      const code = await host.loadExternalVolume(record.datUrl, record.rawUrl)
      if (code !== 0) throw new Error(`External volume load failed (code ${code})`)
      // If the caller wanted the external slot, select the freshly reported index.
      // The state patch arrives async; poll the ref for a bounded time.
      const start = Date.now()
      while (Date.now() - start < 5000) {
        const current = paramsRef.current.dataset
        if (wantDataset < BUILTIN_DATASETS.length || current >= BUILTIN_DATASETS.length) break
        await new Promise((r) => setTimeout(r, 120))
      }
    },
    [],
  )

  const bootControllerRef = useRef<AbortController | null>(null)
  const bootPreviewRef = useRef<() => void>(() => {})
  const lastContextRebootRef = useRef(0)
  // Closed until the first post-boot push is sent; while closed, applyPatch
  // drops stale shell-default echoes so they can't clobber stored params or
  // the pre-reboot panel state.
  const bootPushedRef = useRef(false)

  const onPreviewContextLost = useCallback(() => {
    snackbar('Preview GPU context was lost — waiting for it to restore, then rebooting the preview.', {
      severity: 'error',
      duration: 'long',
      dismissible: true,
    })
  }, [])

  const onPreviewContextRestored = useCallback(() => {
    // A restored context still has dead GL resources (volume textures etc.),
    // so re-pushing controls cannot repaint. Reboot the shell instead — the
    // boot path re-uploads everything and re-applies the panel snapshot.
    // Guard against reboot storms when the GPU keeps failing.
    const now = Date.now()
    if (now - lastContextRebootRef.current < 10000) {
      snackbar('Preview GPU context keeps failing — reload the page to restart the preview.', {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
      return
    }
    lastContextRebootRef.current = now
    snackbar('Preview GPU context restored — rebooting the preview engine.', {
      severity: 'info',
      duration: 'long',
      dismissible: true,
    })
    bootPreviewRef.current()
  }, [])

  // Boot (and reboot) the Wasm shell. Re-entrant: aborts any in-flight boot,
  // disposes the old host, and forces the iframe to reload the shell so GL
  // resources are recreated from scratch.
  const bootPreview = useCallback(() => {
    const iframe = iframeRef.current
    if (!iframe) return
    bootControllerRef.current?.abort()
    hostRef.current?.dispose()
    hostRef.current = null
    bootPushedRef.current = false
    setWasmReady(false)
    setWasmMissing(false)
    // Force a real navigation: re-assigning the same shell URL would be a no-op.
    iframe.src = 'about:blank'
    const controller = new AbortController()
    bootControllerRef.current = controller
    let disposed = false
    const markDisposed = () => {
      disposed = true
    }
    controller.signal.addEventListener('abort', markDisposed)
    void createWasmHost(iframe, {
      signal: controller.signal,
      onState: (patch) => {
        if (!disposed) applyPatch(patch)
      },      onContextLost: () => {
        if (!disposed) onPreviewContextLost()
      },
      onContextRestored: () => {
        if (!disposed) onPreviewContextRestored()
      },
    })
      .then(async (host) => {
        if (disposed) {
          host.dispose()
          return
        }
        hostRef.current = host
        setWasmReady(true)
        setWasmMissing(false)
        // Load the external slot BEFORE the first push: pushing a dataset
        // index into an empty MEMFS makes C++ fall back to defaults and the
        // follow-up full-state echoes would clobber the panel.
        const known = previewImportRef.current
        if (known) {
          try {
            await ensureExternalLoaded(host, known, paramsRef.current.dataset)
          } catch {
            /* surfaced by the import flow; boot continues with builtins */
          }
          if (disposed) return
          // Let the load's echoes flush through React, so the push below
          // carries the discovered slot index rather than a stale one.
          await new Promise((r) => setTimeout(r, 300))
          if (disposed) return
        }
        // First push also opens the echo gate (same tick — no echo can slip in).
        bootPushedRef.current = true
        noteSent(paramsRef.current)
        host.applyControls(paramsRef.current)
      })
      .catch((err) => {
        if (disposed) return
        if (err instanceof WasmEngineMissingError) {
          setWasmMissing(true)
        } else if (err instanceof DOMException && err.name === 'AbortError') {
          return
        } else {
          snackbar(`Preview failed to load: ${err instanceof Error ? err.message : String(err)}`, {
            severity: 'error',
            duration: 'long',
            dismissible: true,
          })
        }
      })
  }, [applyPatch, ensureExternalLoaded, noteSent, onPreviewContextLost, onPreviewContextRestored])

  useEffect(() => {
    bootPreviewRef.current = bootPreview
  }, [bootPreview])

  // Boot the Wasm shell once.
  useEffect(() => {
    bootPreview()
    const iframe = iframeRef.current
    return () => {
      bootControllerRef.current?.abort()
      bootControllerRef.current = null
      hostRef.current?.dispose()
      hostRef.current = null
      if (iframe) iframe.src = 'about:blank'
    }
    // Boot once; later dataset/import changes use the live host.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootPreview])

  const handleImportPick = useCallback(async () => {
    setImporting(true)
    try {
      const picked = await pickVolumeFile()
      if (picked.cancelled || !picked.path) return
      const record = await importVolumeFile(picked.path)
      onPreviewImport(record)
      onSourceChange(record.source)
      // Immediately load into the preview slot and switch to it.
      const host = hostRef.current
      if (host) {
        await ensureExternalLoaded(host, record, BUILTIN_DATASETS.length)
        // Optimistically select the external slot; the precise index arrives via state.
        setParams((prev) => ({ ...prev, dataset: externalIndex ?? BUILTIN_DATASETS.length }))
        snackbar(`External volume loaded: ${record.source}`, {
          severity: 'info',
          duration: 'long',
          dismissible: true,
        })
      } else {
        // Host not ready yet — remember the dataset; boot effect will load it.
        setParams((prev) => ({ ...prev, dataset: BUILTIN_DATASETS.length }))
      }
    } catch (err) {
      snackbar(`Volume import failed: ${err instanceof Error ? err.message : String(err)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    } finally {
      setImporting(false)
    }
  }, [ensureExternalLoaded, externalIndex, onPreviewImport, onSourceChange])

  const handleDatasetChange = useCallback(
    (index: number) => {
      const host = hostRef.current
      if (index >= BUILTIN_DATASETS.length) {
        const record = previewImportRef.current
        if (!record) {
          void handleImportPick()
          return
        }
        // Pure state update; the preview effect pushes it (same as every path).
        setParams((prev) => ({ ...prev, dataset: externalIndex ?? BUILTIN_DATASETS.length }))
        if (host) {
          if (externalIndex == null) {
            void ensureExternalLoaded(host, record, BUILTIN_DATASETS.length).catch((err) => {
              snackbar(`External preview failed: ${err instanceof Error ? err.message : String(err)}`, {
                severity: 'error',
                duration: 'long',
                dismissible: true,
              })
            })
          }
        }
        return
      }
      setParams((prev) => ({ ...prev, dataset: index }))
    },
    [ensureExternalLoaded, externalIndex, handleImportPick],
  )

  const updateParams = useCallback(
    (patch: Partial<PreviewParams>) => {
      // Into the rAF mailbox: at most one commit per frame, always the latest
      // values, so the slider thumb can never snap back to a stale snapshot.
      queueParamsPatch(patch)
    },
    [queueParamsPatch],
  )

  const handleRun = useCallback(async () => {
    const host = hostRef.current
    if (!host || !wasmReady) {
      snackbar('Preview engine is not ready yet.', { severity: 'info', duration: 'long', dismissible: true })
      return
    }
    if (runBusy) return
    // Flush any pending debounced sync so Run executes the settled GUI state,
    // then validate the exact script (a script error never reaches Wasm).
    // flushGscSync returns the drained snapshot — paramsRef may lag a frame.
    const snap = flushGscSync()
    const script = syncParamsToScript(gscRef.current, snap)
    if (script !== gscRef.current) setGscText(script)
    const issues = validateGscSource(script, GSC_LANGUAGE)
    editorIssuesRef.current = issues
    if (issues.length > 0) {
      setDiagnostics({ message: formatValidationSummary(issues), isError: true })
      return
    }
    setRunBusy(true)
    setDiagnostics({ message: 'Rendering preview…', isError: false })
    try {
      // Reset semantics: reset the renderer, restore the current dataset,
      // then execute the script. State after the run is read back via callbacks.
      host.reset()
      await new Promise((r) => setTimeout(r, 120))
      // Restore dataset only; the script owns every other knob.
      host.restoreDataset(snap.dataset)
      await new Promise((r) => setTimeout(r, 60))
      const code = await host.runScript(script)
      completedRunRef.current = true
      if (code === 0) {
        setDiagnostics({ message: 'Ran — panel reflects Wasm state', isError: false })
      } else {
        setDiagnostics({ message: `Load failed: code ${code}`, isError: true })
      }
    } catch (err) {
      completedRunRef.current = true
      setDiagnostics({
        message: `Run failed: ${err instanceof Error ? err.message : String(err)}`,
        isError: true,
      })
    } finally {
      setRunBusy(false)
    }
  }, [runBusy, wasmReady, flushGscSync])

  const canRun = editorReady && wasmReady && !runBusy

  const handleSnapshot = useCallback(async () => {
    const host = hostRef.current
    if (!host || !wasmReady) {
      snackbar('Preview engine is not ready yet.', { severity: 'info', duration: 'long', dismissible: true })
      return
    }
    setSnapshotBusy(true)
    try {
      const blob = await host.captureScreenshot()
      const name = `${stageStamp(paramsRef.current).base}.png`
      downloadBlob(blob, name)
      snackbar(`Snapshot saved to your downloads as ${name}.`, {
        severity: 'info',
        duration: 'long',
        dismissible: true,
      })
    } catch (err) {
      snackbar(`Snapshot failed: ${err instanceof Error ? err.message : String(err)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    } finally {
      setSnapshotBusy(false)
    }
  }, [wasmReady])

  // Top-bar action: download the current .gsc script plus the live preview PNG.
  // Both use anchor downloads so they land in the browser download folder.
  const handleExportAll = useCallback(async () => {
    const host = hostRef.current
    if (!host || !wasmReady) {
      snackbar('Preview engine is not ready yet.', { severity: 'info', duration: 'long', dismissible: true })
      return
    }
    if (snapshotBusy) return
    setSnapshotBusy(true)
    try {
      // Settle the debounced sync first so the downloaded .gsc matches the panel.
      const snap = flushGscSync()
      const script = syncParamsToScript(gscRef.current, snap)
      if (script !== gscRef.current) setGscText(script)
      const base = stageStamp(snap).base
      const gscName = `${base}.gsc`
      const pngName = `${base}.png`
      downloadBlob(new Blob([script], { type: 'text/plain;charset=utf-8' }), gscName)
      const shot = await host.captureScreenshot()
      downloadBlob(shot, pngName)
      snackbar(`Saved ${gscName} and ${pngName} to your downloads.`, {
        severity: 'info',
        duration: 'long',
        dismissible: true,
      })
    } catch (err) {
      snackbar(`Download failed: ${err instanceof Error ? err.message : String(err)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    } finally {
      setSnapshotBusy(false)
    }
  }, [snapshotBusy, wasmReady, flushGscSync])

  const exportAllRef = useRef(handleExportAll)
  exportAllRef.current = handleExportAll
  useEffect(() => {
    const onExport = () => {
      void exportAllRef.current()
    }
    window.addEventListener(STAGE_EXPORT_EVENT, onExport)
    return () => window.removeEventListener(STAGE_EXPORT_EVENT, onExport)
  }, [])

  const externalLabel = previewImport ? `External — ${previewImport.source.split(/[\\/]/).pop()}` : 'External volume (import…)'
  const currentDataset = params.dataset
  const datasetTriggerLabel = currentDataset < BUILTIN_DATASETS.length ? datasetLabel(currentDataset) : externalLabel
  const virtualDisabled = !isVirtualMethod(params.method)

  return (
    <div className="gav-live">
      {wasmMissing ? (
        <EmptyState
          title="Wasm preview assets are missing"
          description={
            <>
              <code>/wasm/sampling.js</code> is not built yet. Build it once from the Raycaster sources — it is
              intentionally not rebuilt on every <code>npm run dev</code>. Then reload this page; the 8 built-in
              datasets load from the bundled .data.
              <CodeBlock variant="plain" code="npm run wasm:build" />
            </>
          }
          actions={
            <Button variant="primary" size="sm" onClick={() => window.location.reload()}>
              Retry after build
            </Button>
          }
        />
      ) : (
        <div className="gav-canvas-row">
          <div className="gav-canvas-host">
            <iframe ref={iframeRef} title="Raycaster live preview" className="gav-wasm-frame" src="about:blank" />
            <div className="fynns-control-cluster fynns-control-cluster--end-align">
              <Tooltip content="Download current preview as PNG">
                <IconButton
                  variant="ghost"
                  size="sm"
                  aria-label="Download current preview as PNG"
                  disabled={!wasmReady || snapshotBusy}
                  onClick={() => void handleSnapshot()}
                >
                  <DownloadIcon aria-hidden />
                </IconButton>
              </Tooltip>
            </div>
          </div>

          <Card title="Rendering parameters">
            <FieldStack>
              <FieldBlock label="Dataset">
                <DropdownMenu trigger={datasetTriggerLabel} ariaLabel="Dataset">
                  {BUILTIN_DATASETS.map((_, i) => (
                    <DropdownMenuItem
                      key={i}
                      icon={currentDataset === i ? <CheckIcon aria-hidden /> : undefined}
                      onClick={() => handleDatasetChange(i)}
                    >
                      {datasetLabel(i)}
                    </DropdownMenuItem>
                  ))}
                  <DropdownMenuItem
                    icon={currentDataset >= BUILTIN_DATASETS.length ? <CheckIcon aria-hidden /> : undefined}
                    onClick={() => handleDatasetChange(BUILTIN_DATASETS.length)}
                  >
                    {externalLabel}
                  </DropdownMenuItem>
                </DropdownMenu>
              </FieldBlock>

              <ControlBlock
                description={
                  previewImport
                    ? `Current external: ${previewImport.source}`
                    : source
                      ? `Agent source: ${source} — import to preview it`
                      : 'Pick a header; conversion reuses volume.py render.dat/raw.'
                }
              >
                <ControlRow label="Volume file (.dat/.nhdr/.nrrd)">
                  <Button variant="ghost" size="sm" disabled={importing} onClick={() => void handleImportPick()}>
                    {importing ? 'Importing…' : 'Import & preview'}
                  </Button>
                </ControlRow>
              </ControlBlock>

              <FieldBlock label="Method">
                <DropdownMenu trigger={`${params.method}: ${RENDER_METHODS[params.method]}`} ariaLabel="Method">
                  {RENDER_METHODS.map((m, i) => (
                    <DropdownMenuItem
                      key={i}
                      icon={params.method === i ? <CheckIcon aria-hidden /> : undefined}
                      onClick={() => updateParams({ method: i })}
                    >
                      {i}: {m}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenu>
              </FieldBlock>

              <FieldBlock label="Sampling rate">
                <DropdownMenu
                  trigger={`${formatSamplingRate(params.samplingRateExp)}× (2^${params.samplingRateExp})`}
                  ariaLabel="Sampling rate"
                >
                  {SAMPLING_RATE_EXPS.map((exp) => (
                    <DropdownMenuItem
                      key={exp}
                      icon={params.samplingRateExp === exp ? <CheckIcon aria-hidden /> : undefined}
                      onClick={() => updateParams({ samplingRateExp: exp })}
                    >
                      {formatSamplingRate(exp)}× (2^{exp})
                    </DropdownMenuItem>
                  ))}
                </DropdownMenu>
              </FieldBlock>

              <FieldBlock label="Virtual samples" actions={<span>{params.virtualSamples}</span>}>
                <Slider
                  value={params.virtualSamples}
                  min={1}
                  max={50}
                  step={1}
                  disabled={virtualDisabled}
                  ariaLabel="Virtual samples"
                  onChange={(v) => updateParams({ virtualSamples: clampInt(v, 1, 50, 1) })}
                />
              </FieldBlock>

              <Checkbox
                label="Constant sample count"
                checked={params.constantSampleCount}
                disabled={virtualDisabled}
                onCheckedChange={(next) => updateParams({ constantSampleCount: next })}
              />
              <Checkbox
                label="Orthographic projection"
                checked={params.ortho}
                onCheckedChange={(next) => updateParams({ ortho: next })}
              />
              <Checkbox
                label="Early ray termination (α 0.99)"
                checked={params.ertEnabled}
                onCheckedChange={(next) => updateParams({ ertEnabled: next })}
              />

              <FieldBlock label="Transfer function">
                <DropdownMenu
                  trigger={params.tfType === 1 ? 'Gaussian' : params.tfType === 2 ? 'Custom' : 'Smoothstep'}
                  ariaLabel="Transfer function"
                >
                  <DropdownMenuItem
                    icon={params.tfType === 0 ? <CheckIcon aria-hidden /> : undefined}
                    onClick={() => updateParams({ tfType: 0 })}
                  >
                    Smoothstep
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    icon={params.tfType === 1 ? <CheckIcon aria-hidden /> : undefined}
                    onClick={() => updateParams({ tfType: 1 })}
                  >
                    Gaussian
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    icon={params.tfType === 2 ? <CheckIcon aria-hidden /> : undefined}
                    onClick={() => updateParams({ tfType: 2 })}
                  >
                    Custom
                  </DropdownMenuItem>
                </DropdownMenu>
              </FieldBlock>

              <FieldBlock label="TF start" actions={<span>{params.tfStart.toFixed(3)}</span>}>
                <Slider
                  value={params.tfStart}
                  min={0}
                  max={1}
                  step={0.001}
                  ariaLabel="TF start"
                  onChange={(v) => updateParams({ tfStart: Math.min(1, Math.max(0, v)) })}
                />
              </FieldBlock>
              <FieldBlock label="TF width" actions={<span>{params.tfWidth.toFixed(3)}</span>}>
                <Slider
                  value={params.tfWidth}
                  min={0.001}
                  max={1}
                  step={0.001}
                  ariaLabel="TF width"
                  onChange={(v) => updateParams({ tfWidth: Math.min(1, Math.max(0.00001, v)) })}
                />
              </FieldBlock>

              <FieldBlock label="Level" actions={<span>{params.level}</span>}>
                <Slider
                  value={params.level}
                  min={0}
                  max={4}
                  step={1}
                  ariaLabel="Level"
                  onChange={(v) => updateParams({ level: clampInt(v, 0, 8, 0) })}
                />
              </FieldBlock>
            </FieldStack>
            <CodeBlock label="Transform (readback)" code={params.transform || 'identity'} />
          </Card>
        </div>
      )}

      <Card title="Custom transfer function">
        <CustomTfEditor
          encoding={params.customTf}
          tfStart={params.tfStart}
          tfWidth={params.tfWidth}
          onChange={(encoding) => {
            // Into the rAF mailbox; the flush bailout covers unchanged values.
            updateParams({ customTf: encoding, tfType: 2 as const })
          }}
        />
      </Card>

      <Collapsible
        title="GSC"
        chrome="plain"
        icon={<Code aria-hidden size={16} />}
        defaultOpen
        actions={
          <>
            <Tooltip content="Run the current script (Ctrl+Enter)">
              <IconButton
                aria-label="Run"
                disabled={!canRun}
                onClick={() => void handleRun()}
              >
                <Rocket aria-hidden size={16} strokeWidth={2} />
              </IconButton>
            </Tooltip>
            <Tooltip content="GSC language reference">
              <IconButton
                variant="ghost"
                size="sm"
                aria-label="Open GSC language reference"
                onClick={() => setReferenceOpen(true)}
              >
                <InfoIcon aria-hidden />
              </IconButton>
            </Tooltip>
          </>
        }
      >
        <div className="gsc-pane-card-body gsc-gsc-body">
          <div className="gsc-script-pane">
            <GscEditor
              value={gscText}
              onChange={setGscText}
              onReady={handleEditorReady}
              onRunShortcut={() => void handleRun()}
              onValidationChange={handleEditorValidation}
            />
          </div>
          <Surface variant="outlined" padded className="gsc-diagnostics-well">
            <p
              className={[
                'gsc-diagnostics',
                'fynns-scroll',
                diagnostics.isError ? 'gsc-diagnostics--error' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              role={diagnostics.isError ? 'alert' : 'status'}
            >
              {diagnostics.message || 'Standby'}
            </p>
          </Surface>
        </div>
      </Collapsible>
      <GscReferenceDialog open={referenceOpen} onOpenChange={setReferenceOpen} />
    </div>
  )
}
