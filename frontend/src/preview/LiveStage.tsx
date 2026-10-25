import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  CheckIcon,
  CodeBlock,
  ControlBlock,
  ControlRow,
  DropdownMenu,
  DropdownMenuItem,
  DownloadIcon,
  EmptyState,
  FieldBlock,
  FieldHint,
  FieldStack,
  IconButton,
  InfoIcon,
  Slider,
  Textarea,
  Tooltip,
  snackbar,
} from '@fynns/ui'
import { importVolumeFile, pickVolumeFile, type VolumeImport } from '../api'
import { GscReferenceDialog } from './gscReference'
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

const RUN_SHORTCUT_HINT = 'Ctrl/Cmd+Enter'

/** Window event fired by the top bar to download .gsc + preview PNG. */
export const STAGE_EXPORT_EVENT = 'gav:export-stage'

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
  const [wasmReady, setWasmReady] = useState(false)
  const [wasmMissing, setWasmMissing] = useState(false)
  const [runBusy, setRunBusy] = useState(false)
  const [runResult, setRunResult] = useState<string>('Standby')
  const [importing, setImporting] = useState(false)
  const [snapshotBusy, setSnapshotBusy] = useState(false)
  const [externalIndex, setExternalIndex] = useState<number | null>(null)
  const [referenceOpen, setReferenceOpen] = useState(false)

  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const hostRef = useRef<WasmHost | null>(null)
  const paramsRef = useRef(params)
  const gscRef = useRef(gscText)
  const previewImportRef = useRef(previewImport)
  paramsRef.current = params
  gscRef.current = gscText
  previewImportRef.current = previewImport

  // Autosave editor + params.
  useEffect(() => {
    storeDraft(gscText)
  }, [gscText])
  useEffect(() => {
    storeParams(params)
    try {
      localStorage.setItem(GSC_DATASET_KEY, String(params.dataset))
    } catch {
      /* ignore */
    }
  }, [params])

  const applyPatch = useCallback((patch: WasmStatePatch) => {
    setParams((prev) => {
      const next: PreviewParams = { ...prev }
      let changed = false
      if (typeof patch.dataset === 'number' && Number.isFinite(patch.dataset)) {
        const v = clampInt(patch.dataset, 0, 64, prev.dataset)
        if (v !== prev.dataset) {
          next.dataset = v
          changed = true
        }
        if (v >= BUILTIN_DATASETS.length) {
          setExternalIndex(v)
        }
      }
      if (typeof patch.method === 'number' && Number.isFinite(patch.method)) {
        const v = clampInt(patch.method, 0, RENDER_METHODS.length - 1, prev.method)
        if (v !== prev.method) {
          next.method = v
          changed = true
        }
      }
      if (typeof patch.samplingRateExp === 'number' && Number.isFinite(patch.samplingRateExp)) {
        const v = clampInt(patch.samplingRateExp, -5, 6, prev.samplingRateExp)
        if (v !== prev.samplingRateExp) {
          next.samplingRateExp = v
          changed = true
        }
      }
      if (typeof patch.virtualSamples === 'number' && Number.isFinite(patch.virtualSamples)) {
        const v = clampInt(patch.virtualSamples, 1, 50, prev.virtualSamples)
        if (v !== prev.virtualSamples) {
          next.virtualSamples = v
          changed = true
        }
      }
      if (typeof patch.constantSampleCount === 'boolean' && patch.constantSampleCount !== prev.constantSampleCount) {
        next.constantSampleCount = patch.constantSampleCount
        changed = true
      }
      if (typeof patch.ortho === 'boolean' && patch.ortho !== prev.ortho) {
        next.ortho = patch.ortho
        changed = true
      }
      if (typeof patch.ertEnabled === 'boolean' && patch.ertEnabled !== prev.ertEnabled) {
        next.ertEnabled = patch.ertEnabled
        changed = true
      }
      if (typeof patch.tfType === 'number' && (patch.tfType === 0 || patch.tfType === 1 || patch.tfType === 2)) {
        if (patch.tfType !== prev.tfType) {
          next.tfType = patch.tfType
          changed = true
        }
      }
      if (typeof patch.tfStart === 'number' && Number.isFinite(patch.tfStart)) {
        const v = Math.min(1, Math.max(0, patch.tfStart))
        if (Math.abs(v - prev.tfStart) > 1e-6) {
          next.tfStart = v
          changed = true
        }
      }
      if (typeof patch.tfWidth === 'number' && Number.isFinite(patch.tfWidth)) {
        const v = Math.min(1, Math.max(0.00001, patch.tfWidth))
        if (Math.abs(v - prev.tfWidth) > 1e-6) {
          next.tfWidth = v
          changed = true
        }
      }
      if (typeof patch.customTf === 'string' && patch.customTf !== prev.customTf) {
        next.customTf = patch.customTf
        changed = true
      }
      if (typeof patch.transform === 'string' && patch.transform !== prev.transform) {
        next.transform = patch.transform
        changed = true
      }
      if (typeof patch.level === 'number' && Number.isFinite(patch.level)) {
        const v = clampInt(patch.level, 0, 8, prev.level)
        if (v !== prev.level) {
          next.level = v
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [])

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

  // Boot the Wasm shell once.
  useEffect(() => {
    const iframe = iframeRef.current
    if (!iframe) return
    const controller = new AbortController()
    let disposed = false
    void createWasmHost(iframe, {
      signal: controller.signal,
      onState: (patch) => {
        if (!disposed) applyPatch(patch)
      },
    })
      .then((host) => {
        if (disposed) {
          host.dispose()
          return
        }
        hostRef.current = host
        setWasmReady(true)
        setWasmMissing(false)
        // Restore current params after boot.
        host.applyControls(paramsRef.current)
        // If an external import is already known, load it so the slot exists.
        const known = previewImportRef.current
        if (known) {
          void ensureExternalLoaded(host, known, paramsRef.current.dataset).catch(() => undefined)
        }
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
    return () => {
      disposed = true
      controller.abort()
      hostRef.current?.dispose()
      hostRef.current = null
      if (iframe) iframe.src = 'about:blank'
    }
    // Boot once; later dataset/import changes use the live host.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applyPatch, ensureExternalLoaded])

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
        setParams((prev) => ({ ...prev, dataset: externalIndex ?? BUILTIN_DATASETS.length }))
        if (host) {
          if (externalIndex != null) {
            host.applyControls({ ...paramsRef.current, dataset: externalIndex })
          } else {
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
      host?.applyControls({ ...paramsRef.current, dataset: index })
    },
    [ensureExternalLoaded, externalIndex, handleImportPick],
  )

  const updateParams = useCallback(
    (patch: Partial<PreviewParams>) => {
      setParams((prev) => {
        const next = { ...prev, ...patch }
        hostRef.current?.applyControls(next)
        return next
      })
    },
    [],
  )

  const handleRun = useCallback(async () => {
    const host = hostRef.current
    if (!host || !wasmReady) {
      snackbar('Preview engine is not ready yet.', { severity: 'info', duration: 'long', dismissible: true })
      return
    }
    if (runBusy) return
    setRunBusy(true)
    setRunResult('Rendering preview…')
    try {
      // Reset semantics: reset the renderer, restore the current dataset,
      // then execute the script. State after the run is read back via callbacks.
      host.reset()
      await new Promise((r) => setTimeout(r, 120))
      const current = paramsRef.current
      // Restore dataset only; the script owns every other knob.
      host.restoreDataset(current.dataset)
      await new Promise((r) => setTimeout(r, 60))
      const code = await host.runScript(gscRef.current)
      if (code === 0) {
        setRunResult('Ran — panel reflects Wasm state')
      } else {
        setRunResult(`Load failed: code ${code}`)
      }
    } catch (err) {
      setRunResult(`Run failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setRunBusy(false)
    }
  }, [runBusy, wasmReady])

  const handleSyncParams = useCallback(() => {
    const next = syncParamsToScript(gscRef.current, paramsRef.current)
    setGscText(next)
    snackbar('Preview params written to the generated zone.', { severity: 'info', duration: 'long', dismissible: true })
  }, [])

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
      const base = stageStamp(paramsRef.current).base
      const gscName = `${base}.gsc`
      const pngName = `${base}.png`
      downloadBlob(new Blob([gscRef.current], { type: 'text/plain;charset=utf-8' }), gscName)
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
  }, [snapshotBusy, wasmReady])

  const exportAllRef = useRef(handleExportAll)
  exportAllRef.current = handleExportAll
  useEffect(() => {
    const onExport = () => {
      void exportAllRef.current()
    }
    window.addEventListener(STAGE_EXPORT_EVENT, onExport)
    return () => window.removeEventListener(STAGE_EXPORT_EVENT, onExport)
  }, [])

  const handleGscKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        void handleRun()
      }
    },
    [handleRun],
  )

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
            setParams((prev) => {
              const next = { ...prev, customTf: encoding, tfType: 2 as const }
              hostRef.current?.applyControls(next)
              return next
            })
          }}
        />
      </Card>

      <Card
        title="GSC Code Editor"
        actions={
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
        }
      >
        <div className="fynns-control-cluster fynns-control-cluster--start-align">
          <Tooltip content={`Run the script (${RUN_SHORTCUT_HINT})`}>
            <Button variant="primary" size="sm" disabled={!wasmReady || runBusy} onClick={() => void handleRun()}>
              {runBusy ? 'Running…' : 'Run'}
            </Button>
          </Tooltip>
          <Button variant="ghost" size="sm" onClick={handleSyncParams}>
            Sync params to script
          </Button>
        </div>
        <FieldHint>{runResult}</FieldHint>
        <Textarea
          autoGrow={false}
          rows={14}
          aria-label="GSC script"
          value={gscText}
          onChange={(e) => setGscText(e.target.value)}
          onKeyDown={handleGscKeyDown}
        />
        <FieldHint>
          Draft + params autosave locally. Run resets the renderer, restores the current dataset, executes the script,
          then reads panel state back from Wasm. Sync only rewrites the marked generated zone.
        </FieldHint>
      </Card>
      <GscReferenceDialog open={referenceOpen} onOpenChange={setReferenceOpen} />
    </div>
  )
}
