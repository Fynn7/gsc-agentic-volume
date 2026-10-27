/**
 * Volume-agent GUI chat: the coding-agent skin over the Codex launcher runs.
 *
 * Data model (single track, same as the Textual TUI): runs discovered by
 * scripts/volume_tui, each rendered from its server-built transcript blocks
 * (transcript.py + event_text.py wording, never reworded client-side).
 * The old Session/OllamaDriver model (api.ts SessionData + ChatRows.tsx) is
 * gone; the Live Preview Stage stays as the volume workbench and writes the
 * shared settings volume.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import {
  applyFynnsThemeMode,
  BotIcon,
  BrainIcon,
  BusyRegion,
  Button,
  Card,
  Chat,
  ChatComposer,
  ChatScrollToBottom,
  ChatThread,
  ClippedNavShell,
  CodeBlock,
  Collapsible,
  ConfirmDialog,
  Dialog,
  DownloadIcon,
  EmptyState,
  EndAside,
  ExpandableCodeBlock,
  FileIcon,
  FillColumn,
  getFynnsThemeMode,
  IconButton,
  LinearProgress,
  List,
  ListItem,
  LoadingSkeleton,
  MenuIcon,
  NavigationDrawer,
  NavigationDrawerItem,
  NavigationDrawerNewChat,
  PageScroll,
  PanelLeftIcon,
  PanelRightIcon,
  PlusIcon,
  restoreFynnsThemeMode,
  SnackbarHost,
  SplitPane,
  Tabs,
  TopAppBar,
  Tooltip,
  snackbar,
} from '@fynns/ui'
import { importVolumeFile, pickVolumeFile, type VolumeImport } from './api'
import { LiveStage, STAGE_EXPORT_EVENT } from './preview/LiveStage'
import { TuiBlockRow, type ZoomPayload } from './TuiThread'
import {
  BatchDetailDialog,
  BatchDialog,
  GlossaryDialog,
  HelpDialog,
  HistoryDialog,
  SettingsDialog,
  VolumesDialog,
} from './TuiDialogs'
import { matchSlashCommands, parseSlashCommand, type SlashAction } from './slash'
import {
  errorText,
  tuiArchiveAll,
  tuiBatchDetail,
  tuiCancelBatch,
  tuiCopyRun,
  tuiDownloadChatLog,
  tuiGlossary,
  tuiListRuns,
  tuiLoadSettings,
  tuiOpenPath,
  tuiRunImages,
  tuiSaveSettings,
  tuiStartBatch,
  tuiStartRun,
  tuiStopRun,
  tuiTranscript,
  tuiVerifyRun,
  tuiVerifyState,
  tuiVolumes,
  type TuiBatchRecord,
  type TuiGlossaryEntry,
  type TuiRun,
  type TuiSettings,
  type TuiTranscript,
  type TuiVolume,
} from './tuiApi'

const POLL_INTERVAL_MS = 1500
const RUNS_POLL_MS = 3000
const STAGE_WIDTH_KEY = 'gav-stage-width-v2'
const STAGE_OPEN_KEY = 'gav-stage-open'
const HISTORY_KEY = 'gav-composer-history'

type Pending = null | 'starting' | 'stopping'

type VerifyState = {
  running: boolean
  result: { code: number | null; summary: string[]; report: unknown; output: string } | null
}

function readStageWidth(): number {
  try {
    const raw = localStorage.getItem(STAGE_WIDTH_KEY)
    if (raw != null) {
      const v = Number.parseFloat(raw)
      if (Number.isFinite(v) && v >= 30 && v <= 75) return v
    }
  } catch {
    /* ignore */
  }
  return 50
}

function readStageOpen(): boolean {
  try {
    const raw = localStorage.getItem(STAGE_OPEN_KEY)
    if (raw === '0' || raw === 'false') return false
  } catch {
    /* ignore */
  }
  return true
}

function loadHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === 'string') : []
  } catch {
    return []
  }
}

function runStateLabel(status: string, pending: Pending): string {
  if (pending === 'starting') return 'Starting'
  if (pending === 'stopping') return 'Stop requested'
  switch (status) {
    case 'running':
      return 'Running'
    case 'finished':
      return 'Finished — claim unverified'
    case 'failed':
      return 'Failed'
    case 'stopped':
    case 'interrupted':
      return 'Stopped'
    default:
      return status || 'Unknown'
  }
}

function liveLabel(transcript: TuiTranscript | null, pending: Pending): string {
  if (pending === 'starting') return 'Starting the run — reserving the directory and writing the config'
  if (pending === 'stopping') return 'Stop requested — waiting for the launcher to notice the stop marker'
  if (!transcript || transcript.blocks.length === 0) return 'Preparing the run'
  const last = transcript.blocks[transcript.blocks.length - 1]
  if (last.kind === 'thinking' && last.status === 'running') return 'Model is choosing the next action'
  if (last.kind === 'tool') return 'Renderer is executing the accepted tool call'
  if (last.kind === 'image') return 'Frame captured — model is reading the new picture'
  return 'Waiting for the next recorded event'
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

function App() {
  const [navOpen, setNavOpen] = useState(true)
  const [asideOpen, setAsideOpen] = useState(false)

  const [settings, setSettings] = useState<TuiSettings | null>(null)
  const [source, setSource] = useState('')
  const [draft, setDraft] = useState('')
  const [picking, setPicking] = useState(false)
  const [previewImport, setPreviewImport] = useState<VolumeImport | null>(null)
  const [history, setHistory] = useState<string[]>(() => loadHistory())
  const [histIdx, setHistIdx] = useState<number | null>(null)

  const [runs, setRuns] = useState<TuiRun[]>([])
  const [runsLoaded, setRunsLoaded] = useState(false)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [transcript, setTranscript] = useState<TuiTranscript | null>(null)
  const [pending, setPending] = useState<Pending>(null)
  const startStampRef = useRef(0)

  const [showPlumbing, setShowPlumbing] = useState(false)
  const [slashSel, setSlashSel] = useState(0)

  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [volumesOpen, setVolumesOpen] = useState(false)
  const [volumes, setVolumes] = useState<TuiVolume[]>([])
  const [volumesLoading, setVolumesLoading] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [archiving, setArchiving] = useState(false)
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchStarting, setBatchStarting] = useState(false)
  const [batchDetail, setBatchDetail] = useState<TuiBatchRecord | null>(null)
  const [batchCancelling, setBatchCancelling] = useState(false)
  const [glossaryOpen, setGlossaryOpen] = useState(false)
  const [glossary, setGlossary] = useState<TuiGlossaryEntry[]>([])
  const [helpOpen, setHelpOpen] = useState(false)
  const [quitConfirm, setQuitConfirm] = useState(false)
  const [archiveConfirm, setArchiveConfirm] = useState(false)

  const [verify, setVerify] = useState<VerifyState>({ running: false, result: null })

  const [zoom, setZoom] = useState<ZoomPayload | null>(null)
  const [zoomOpen, setZoomOpen] = useState(false)
  const pollFailed = useRef(false)
  const activeRef = useRef<string | null>(null)
  useEffect(() => {
    activeRef.current = activeRunId
  }, [activeRunId])
  const liveRef = useRef(false)

  // Live Preview workbench: desktop Chat 50% / Stage 50% in a core SplitPane;
  // narrow viewports switch via the top-bar Tabs.
  const [stageOpen, setStageOpen] = useState<boolean>(() => readStageOpen())
  const [stageWidth, setStageWidth] = useState<number>(() => readStageWidth())
  const [narrowView, setNarrowView] = useState<'chat' | 'stage'>('chat')
  const [isNarrow, setIsNarrow] = useState<boolean>(() =>
    typeof window !== 'undefined' ? window.matchMedia('(max-width: 56.25rem)').matches : false,
  )
  const [rowWidth, setRowWidth] = useState<number>(() =>
    typeof window !== 'undefined' ? window.innerWidth : 1280,
  )
  const [remPx, setRemPx] = useState<number>(16)
  const canvasRef = useRef<HTMLDivElement | null>(null)
  const rowWidthRef = useRef<number>(typeof window !== 'undefined' ? window.innerWidth : 1280)

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 56.25rem)')
    const sync = () => setIsNarrow(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(STAGE_WIDTH_KEY, String(stageWidth))
    } catch {
      /* ignore */
    }
  }, [stageWidth])

  useEffect(() => {
    try {
      localStorage.setItem(STAGE_OPEN_KEY, stageOpen ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [stageOpen])

  useLayoutEffect(() => {
    const row = canvasRef.current
    if (!row) return
    const sync = () => {
      const w = row.clientWidth
      if (w > 0) {
        rowWidthRef.current = w
        setRowWidth(w)
      }
      try {
        const root = Number.parseFloat(getComputedStyle(document.documentElement).fontSize)
        if (Number.isFinite(root) && root > 0) setRemPx(root)
      } catch {
        /* keep default */
      }
    }
    sync()
    const observer = new ResizeObserver(sync)
    observer.observe(row)
    return () => observer.disconnect()
  }, [])

  const chatMinPx = Math.round(23 * remPx)
  const stageMinPx = 320
  const splitChatPx = (() => {
    const raw = Math.round((rowWidth * (100 - stageWidth)) / 100)
    if (!(rowWidth > 0)) return raw
    return Math.min(Math.max(raw, chatMinPx), Math.max(chatMinPx, rowWidth - stageMinPx - 8))
  })()

  const handleSplitSizeChange = useCallback((chatPx: number) => {
    const w = rowWidthRef.current
    if (!(w > 0)) return
    const pct = 100 - (chatPx / w) * 100
    setStageWidth(Math.min(75, Math.max(30, Math.round(pct * 10) / 10)))
  }, [])

  /* -- boot: theme, settings, glossary, volumes, runs -------------------- */

  useEffect(() => {
    try {
      restoreFynnsThemeMode()
    } catch {
      /* keep dark */
    }
    void tuiLoadSettings()
      .then((loaded) => {
        setSettings(loaded)
        setSource(loaded.volumePath || '')
      })
      .catch((error: unknown) => {
        snackbar(`Settings are unreadable: ${errorText(error)}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
      })
    void tuiGlossary()
      .then((data) => setGlossary(data.glossary))
      .catch(() => undefined)
    void tuiVolumes()
      .then((data) => setVolumes(data.volumes))
      .catch(() => undefined)
  }, [])

  const ensureVolumes = useCallback(async () => {
    setVolumesLoading(true)
    try {
      const data = await tuiVolumes()
      setVolumes(data.volumes)
    } catch {
      /* keep the previous list; the dialog shows its empty state */
    } finally {
      setVolumesLoading(false)
    }
  }, [])

  const refreshRuns = useCallback(async () => {
    try {
      const data = await tuiListRuns()
      setRuns(data.runs)
      setRunsLoaded(true)
      return data.runs
    } catch {
      return null
    }
  }, [])

  useEffect(() => {
    void refreshRuns()
    const timer = window.setInterval(() => void refreshRuns(), RUNS_POLL_MS)
    return () => window.clearInterval(timer)
  }, [refreshRuns])

  /* -- transcript polling: the live view follows the recorded evidence ---- */

  useEffect(() => {
    if (!activeRunId) return
    let cancelled = false
    let inFlight = false
    const poll = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const data = await tuiTranscript(activeRunId)
        if (cancelled || activeRef.current !== activeRunId) return
        pollFailed.current = false
        setTranscript(data)
        liveRef.current = data.status === 'running'
        if (pending === 'starting') setPending(null)
        if (pending === 'stopping' && data.status !== 'running') setPending(null)
      } catch (error) {
        if (cancelled) return
        if (!pollFailed.current) {
          pollFailed.current = true
          snackbar(`Could not refresh the run: ${errorText(error)}`, {
            severity: 'error',
            duration: 'long',
            dismissible: true,
          })
        }
      } finally {
        inFlight = false
      }
    }
    void poll()
    const timer = window.setInterval(() => {
      // Finished/failed/stopped freezes the view: no more polling.
      if (!liveRef.current && pending === null) {
        window.clearInterval(timer)
        return
      }
      void poll()
    }, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [activeRunId, pending])

  /* -- run lifecycle ------------------------------------------------------ */

  const openRun = useCallback((runId: string) => {
    setActiveRunId(runId)
    setTranscript(null)
    setVerify({ running: false, result: null })
    setPending(null)
    liveRef.current = true
    setHistoryOpen(false)
  }, [])

  const handleNewTask = useCallback(() => {
    setActiveRunId(null)
    setTranscript(null)
    setVerify({ running: false, result: null })
    setPending(null)
    setDraft('')
  }, [])

  const pushHistory = useCallback((value: string) => {
    const text = value.trim()
    if (!text) return
    setHistory((prev) => {
      const next = [text, ...prev.filter((item) => item !== text)].slice(0, 100)
      try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(next))
      } catch {
        /* ignore */
      }
      return next
    })
    setHistIdx(null)
  }, [])

  const startCustomRun = useCallback(
    async (task: string, taskKind: 'custom' | 'task-a' | 'task-b' = 'custom') => {
      if (!settings) {
        snackbar('Settings are still loading — retry in a second.', {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
        return
      }
      const live = runs.some((run) => run.status === 'Running')
      if (live || pending !== null || liveRef.current) {
        snackbar('A run is still live — stop it first (/new never stops a live run).', {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
        return
      }
      const goal = task.trim()
      if (!goal && taskKind === 'custom') return
      if (!source.trim()) {
        snackbar('Choose the local volume file first.', {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
        void chooseSourceRef.current()
        return
      }
      setPending('starting')
      startStampRef.current = Date.now() / 1000
      try {
        await tuiStartRun({ taskKind, task: goal, settings: { ...settings, volumePath: source.trim() } })
        pushHistory(taskKind === 'custom' ? goal : `/${taskKind}`)
        setDraft('')
        // The launcher mints the run id: adopt the newest run on next poll.
        const stamp = startStampRef.current
        const prevActive = activeRef.current
        let adopted: string | null = null
        for (let attempt = 0; attempt < 20; attempt++) {
          await new Promise((resolve) => setTimeout(resolve, 1000))
          const latest = await refreshRuns()
          const found = (latest ?? []).find((run) => (run.startedAt ?? 0) >= stamp - 1)
          if (found) {
            adopted = found.runId
            openRun(found.runId)
            break
          }
        }
        if (adopted === null) {
          // Fallback: open the newest run when it differs from the previously open one.
          const latest = await refreshRuns()
          if (latest && latest.length > 0 && latest[0].runId !== prevActive) openRun(latest[0].runId)
        }
      } catch (error: unknown) {
        setPending(null)
        snackbar(`Could not start the task: ${errorText(error)}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
      }
    },
    [settings, runs, pending, source, refreshRuns, openRun, pushHistory],
  )

  const handleStop = useCallback(() => {
    if (!activeRunId) return
    setPending('stopping')
    liveRef.current = true // keep polling until the launcher records the end
    void tuiStopRun(activeRunId).catch((error: unknown) => {
      setPending(null)
      snackbar(`Stop was not accepted: ${errorText(error)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    })
  }, [activeRunId])

  const chooseSource = useCallback(async () => {
    setPicking(true)
    try {
      const picked = await pickVolumeFile()
      if (!picked.cancelled && picked.path) {
        setSource(picked.path)
        try {
          const record = await importVolumeFile(picked.path)
          setPreviewImport(record)
        } catch (importError: unknown) {
          snackbar(`Preview import failed: ${errorText(importError)}`, {
            severity: 'error',
            duration: 'long',
            dismissible: true,
          })
        }
      }
    } catch (error: unknown) {
      snackbar(
        `Could not open the file browser: ${errorText(error)}. The picker runs in the local harness — start it with scripts/dev.ps1 and retry.`,
        { severity: 'error', duration: 'long', dismissible: true },
      )
    } finally {
      setPicking(false)
    }
  }, [])
  const chooseSourceRef = useRef(chooseSource)
  chooseSourceRef.current = chooseSource

  const toggleTheme = useCallback(() => {
    // Core owns the mode (data-fynns-theme on <html>); read it back instead
    // of keeping a parallel state that could drift.
    const next = getFynnsThemeMode() === 'light' ? 'dark' : 'light'
    try {
      applyFynnsThemeMode(next)
    } catch {
      /* ignore */
    }
  }, [])

  /* -- slash commands ----------------------------------------------------- */

  const runSlash = useCallback(
    async (action: SlashAction, args: string) => {
      switch (action) {
        case 'new':
          handleNewTask()
          if (args) await startCustomRun(args)
          return
        case 'task-a':
          await startCustomRun('', 'task-a')
          return
        case 'task-b':
          await startCustomRun('', 'task-b')
          return
        case 'verify': {
          if (!activeRunId) {
            snackbar('No open run to verify.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          setVerify({ running: true, result: null })
          try {
            await tuiVerifyRun(activeRunId)
            for (let attempt = 0; attempt < 150; attempt++) {
              await new Promise((resolve) => setTimeout(resolve, 2000))
              const state = await tuiVerifyState(activeRunId)
              if (!state.running && state.result) {
                setVerify({ running: false, result: state.result })
                return
              }
            }
            setVerify((prev) => ({ ...prev, running: false }))
            snackbar('Verify is still running — reopen /verify to read it.', {
              severity: 'info',
              duration: 'long',
              dismissible: true,
            })
          } catch (error: unknown) {
            setVerify({ running: false, result: null })
            snackbar(`Verify failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'copy-json':
        case 'copy-chat': {
          if (!activeRunId) {
            snackbar('No open run to copy.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          try {
            const data = await tuiCopyRun({ runId: activeRunId, kind: action === 'copy-json' ? 'json' : 'chat' })
            snackbar(data.message, { severity: 'info', duration: 'long', dismissible: true })
          } catch (error: unknown) {
            snackbar(`Copy failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'export-chat': {
          if (!activeRunId) {
            snackbar('No open run to export.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          try {
            const file = await tuiDownloadChatLog(activeRunId)
            downloadBlob(new Blob([file.text], { type: 'text/plain;charset=utf-8' }), file.name)
            snackbar(`Saved ${file.name} to your downloads.`, {
              severity: 'info',
              duration: 'long',
              dismissible: true,
            })
          } catch (error: unknown) {
            snackbar(`Export failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'export-images': {
          if (!activeRunId) {
            snackbar('No open run to export.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          try {
            const response = await fetch(`/api/tui/runs/${encodeURIComponent(activeRunId)}/images.zip`)
            if (!response.ok) throw new Error(`Export failed (${response.status})`)
            downloadBlob(await response.blob(), `${activeRunId}-images.zip`)
            snackbar('Saved the frame zip to your downloads.', {
              severity: 'info',
              duration: 'long',
              dismissible: true,
            })
          } catch (error: unknown) {
            snackbar(`Export failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'open-image': {
          if (!activeRunId) {
            snackbar('No open run.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          try {
            const data = await tuiRunImages(activeRunId)
            const latest = data.images[data.images.length - 1]
            if (!latest) {
              snackbar('This run has no frames yet.', { severity: 'info', duration: 'long', dismissible: true })
              return
            }
            const data2 = await tuiOpenPath(String(latest.image))
            snackbar(data2.message, { severity: 'info', duration: 'long', dismissible: true })
          } catch (error: unknown) {
            snackbar(`Open failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'open-folder': {
          if (!transcript) {
            snackbar('No open run.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          try {
            const data = await tuiOpenPath(transcript.directory)
            snackbar(data.message, { severity: 'info', duration: 'long', dismissible: true })
          } catch (error: unknown) {
            snackbar(`Open failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'history':
          void refreshRuns()
          setHistoryOpen(true)
          return
        case 'batch':
          setBatchOpen(true)
          return
        case 'archive-all':
          setArchiveConfirm(true)
          return
        case 'change-volume':
          void ensureVolumes()
          setVolumesOpen(true)
          return
        case 'settings':
          void ensureVolumes()
          setSettingsOpen(true)
          return
        case 'theme':
          toggleTheme()
          return
        case 'help':
          setHelpOpen(true)
          return
        case 'glossary':
          setGlossaryOpen(true)
          return
        case 'hard-stop': {
          if (activeRunId) {
            try {
              await tuiStopRun(activeRunId)
              snackbar('Stop marker written — the launcher kills the agent tree; services stay up.', {
                severity: 'info',
                duration: 'long',
                dismissible: true,
              })
            } catch (error: unknown) {
              snackbar(`Hard stop failed: ${errorText(error)}`, {
                severity: 'error',
                duration: 'long',
                dismissible: true,
              })
            }
          } else {
            snackbar('Nothing live to stop.', { severity: 'info', duration: 'long', dismissible: true })
          }
          return
        }
        case 'quit':
          setQuitConfirm(true)
          return
      }
    },
    [activeRunId, transcript, handleNewTask, startCustomRun, refreshRuns, toggleTheme, ensureVolumes],
  )

  const handleSend = useCallback(
    (value: string) => {
      const parsed = parseSlashCommand(value)
      pushHistory(value)
      setDraft('')
      setHistIdx(null)
      if (parsed) {
        void runSlash(parsed.action, parsed.args)
        return
      }
      void startCustomRun(value)
    },
    [runSlash, startCustomRun, pushHistory],
  )

  /* -- settings / volumes / archive / batch writes -------------------------- */

  const handleSaveSettings = useCallback(
    async (next: TuiSettings) => {
      setSettingsSaving(true)
      try {
        const saved = await tuiSaveSettings(next)
        setSettings(saved)
        setSource(saved.volumePath || '')
        setSettingsOpen(false)
        snackbar('Settings saved to runtime/tui-settings.json.', {
          severity: 'info',
          duration: 'long',
          dismissible: true,
        })
      } catch (error: unknown) {
        snackbar(`Settings not saved: ${errorText(error)}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
      } finally {
        setSettingsSaving(false)
      }
    },
    [],
  )

  const handlePickVolume = useCallback(
    async (volume: TuiVolume) => {
      setSource(volume.path)
      setVolumesOpen(false)
      if (!settings) return
      try {
        const saved = await tuiSaveSettings({ ...settings, volumePath: volume.path })
        setSettings(saved)
        snackbar(`Next run works on ${volume.label}; preview it in the Stage.`, {
          severity: 'info',
          duration: 'long',
          dismissible: true,
        })
      } catch (error: unknown) {
        snackbar(`Volume selected locally; settings not saved: ${errorText(error)}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
      }
    },
    [settings],
  )

  const handleStageSourceChange = useCallback(
    (path: string) => {
      setSource(path)
      // The Stage stays the workbench: its volume becomes the next run's volume.
      if (settings && path !== settings.volumePath) {
        const next = { ...settings, volumePath: path }
        setSettings(next)
        void tuiSaveSettings(next).catch((error: unknown) => {
          snackbar(`Stage volume kept locally; settings not saved: ${errorText(error)}`, {
            severity: 'error',
            duration: 'long',
            dismissible: true,
          })
        })
      }
    },
    [settings],
  )

  const handleArchiveAll = useCallback(async () => {
    setArchiving(true)
    try {
      const result = await tuiArchiveAll()
      setArchiveConfirm(false)
      const latest = await refreshRuns()
      if (activeRunId && !(latest ?? []).some((run) => run.runId === activeRunId)) handleNewTask()
      snackbar(
        `Archived ${result.moved.length} runs${result.skipped_live.length ? `; kept live: ${result.skipped_live.join(', ')}` : ''}.`,
        { severity: 'info', duration: 'long', dismissible: true },
      )
    } catch (error: unknown) {
      snackbar(`Archive failed: ${errorText(error)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    } finally {
      setArchiving(false)
    }
  }, [activeRunId, handleNewTask, refreshRuns])

  const handleStartBatch = useCallback(
    async (count: number, task: string) => {
      if (!settings) return
      setBatchStarting(true)
      try {
        const data = await tuiStartBatch({ count, task, settings: { ...settings, volumePath: source.trim() } })
        setBatchOpen(false)
        const detail = await tuiBatchDetail(data.batchId)
        setBatchDetail(detail)
        snackbar(`Batch ${data.batchId} running — members appear in History.`, {
          severity: 'info',
          duration: 'long',
          dismissible: true,
        })
      } catch (error: unknown) {
        snackbar(`Batch not started: ${errorText(error)}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
      } finally {
        setBatchStarting(false)
      }
    },
    [settings, source],
  )

  /* -- global keys: F1/F2/Ctrl+H/Ctrl+T/Ctrl+Q/Esc ---------------------------- */

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const inField =
        !!target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
      if (event.key === 'F1') {
        event.preventDefault()
        setHelpOpen(true)
        return
      }
      if (event.key === 'F2') {
        event.preventDefault()
        setGlossaryOpen(true)
        return
      }
      if (event.ctrlKey && (event.key === 'h' || event.key === 'H')) {
        event.preventDefault()
        setShowPlumbing((v) => !v)
        return
      }
      if (event.ctrlKey && (event.key === 't' || event.key === 'T')) {
        event.preventDefault()
        toggleTheme()
        return
      }
      if (event.ctrlKey && (event.key === 'q' || event.key === 'Q')) {
        event.preventDefault()
        setQuitConfirm(true)
        return
      }
      if (event.key === 'Escape' && !inField) {
        if (activeRef.current) handleStop()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handleStop, toggleTheme])

  /* -- derived view ---------------------------------------------------------- */

  const openZoom = useCallback((payload: ZoomPayload) => {
    setZoom(payload)
    setZoomOpen(true)
  }, [])

  const live = pending !== null || (transcript != null && transcript.status === 'running')
  const stopAvailable = pending === null && !!activeRunId && transcript?.status === 'running'

  const rows: ReactNode[] = []
  if (transcript) {
    transcript.blocks.forEach((block, index) => {
      const row = (
        <TuiBlockRow key={`${block.kind}-${index}`} block={block} showPlumbing={showPlumbing} onZoom={openZoom} />
      )
      if (row) rows.push(row)
    })
  }

  const tail: ReactNode[] = []
  if (live && transcript) {
    const last = transcript.blocks[transcript.blocks.length - 1]
    const streaming = !last || last.kind === 'thinking' || last.kind === 'tool' || last.status === 'running'
    if (streaming) {
      tail.push(
        <TuiBlockRow
          key="live"
          block={{
            kind: 'thinking',
            title: 'Thinking',
            subtitle: liveLabel(transcript, pending),
            body: '',
            status: 'running',
            ts: null,
            data: { reasoning: true },
          }}
          showPlumbing={showPlumbing}
          onZoom={openZoom}
        />,
      )
    }
  } else if (pending === 'starting') {
    tail.push(
      <TuiBlockRow
        key="live"
        block={{
          kind: 'thinking',
          title: 'Thinking',
          subtitle: liveLabel(transcript, pending),
          body: '',
          status: 'running',
          ts: null,
          data: { reasoning: true },
        }}
        showPlumbing={showPlumbing}
        onZoom={openZoom}
      />,
    )
  }

  const slashOpen = draft.trimStart().startsWith('/')
  const slashMatches = slashOpen ? matchSlashCommands(draft.trim()) : []
  const slashList = slashOpen ? (
    <div className="gav-slash-menu" role="listbox" aria-label="Slash commands">
      {slashMatches.length === 0 ? (
        <div className="gav-slash-empty">No match — Enter sends the text as a task.</div>
      ) : (
        slashMatches.slice(0, 12).map((item, index) => (
          <button
            key={item.slash}
            type="button"
            role="option"
            aria-selected={index === slashSel}
            className={index === slashSel ? 'gav-slash-item gav-slash-item--active' : 'gav-slash-item'}
            onClick={() => {
              pushHistory(draft)
              setDraft('')
              void runSlash(item.action, draft.trim().split(/\s+/).slice(1).join(' '))
            }}
          >
            <span className="gav-slash-name">{item.slash}</span>
            <span className="gav-slash-tip">{item.tooltip}</span>
          </button>
        ))
      )}
    </div>
  ) : null

  const composerKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (slashOpen) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault()
          setSlashSel((sel) => {
            const n = Math.min(slashMatches.length, 12)
            if (n === 0) return 0
            return event.key === 'ArrowDown' ? (sel + 1) % n : (sel - 1 + n) % n
          })
          return
        }
        if (event.key === 'Tab' && slashMatches.length > 0) {
          event.preventDefault()
          const pick = slashMatches[Math.min(slashSel, 11)]
          if (pick) setDraft(`${pick.slash} `)
          setSlashSel(0)
          return
        }
        if (event.key === 'Enter' && slashMatches.length > 0 && !event.shiftKey) {
          // Run the highlighted command instead of submitting raw text.
          const token = draft.trim().split(/\s+/)[0].toLowerCase()
          const exact = slashMatches.some((item) => item.slash === token)
          if (!exact) {
            event.preventDefault()
            const pick = slashMatches[Math.min(slashSel, 11)]
            if (pick) {
              const args = draft.trim().split(/\s+/).slice(1).join(' ')
              pushHistory(draft)
              setDraft('')
              setSlashSel(0)
              void runSlash(pick.action, args)
            }
            return
          }
        }
        if (event.key === 'Escape') {
          event.preventDefault()
          setDraft('')
          setSlashSel(0)
          return
        }
      } else if (!draft.includes('\n')) {
        // Shell-like history recall on single-line drafts (TUI Up/Down).
        if (event.key === 'ArrowUp' && history.length > 0) {
          event.preventDefault()
          const next = histIdx === null ? 0 : Math.min(histIdx + 1, history.length - 1)
          setHistIdx(next)
          setDraft(history[next])
          return
        }
        if (event.key === 'ArrowDown' && histIdx !== null) {
          event.preventDefault()
          if (histIdx === 0) {
            setHistIdx(null)
            setDraft('')
          } else {
            const next = histIdx - 1
            setHistIdx(next)
            setDraft(history[next])
          }
        }
      }
    },
    [slashOpen, slashMatches, slashSel, draft, history, histIdx, pushHistory, runSlash],
  )

  const navToggleLabel = navOpen ? 'Close navigation' : 'Open navigation'
  const asideToggleLabel = asideOpen ? 'Hide inspector' : 'Show inspector'
  const stageToggleLabel = stageOpen ? 'Collapse Live Preview stage' : 'Expand Live Preview stage'
  const chatPane = (
    <div className="gav-chat-pane" data-pane="chat">
      <FillColumn>
        {settings ? (
          <Chat label="Task conversation">
            <ChatThread
              label="Task conversation"
              empty={
                activeRunId && !transcript ? (
                  <LoadingSkeleton label="Loading run evidence" lines={8} />
                ) : (
                  <EmptyState
                    icon={<BrainIcon size={28} aria-hidden />}
                    title="Start a run"
                    description="Describe the task, or type / for presets (/task-a, /task-b), history, batches, verify and exports. Every row is read from the recorded run evidence; model claims stay unverified."
                  />
                )
              }
            >
              {rows}
              {tail}
            </ChatThread>
            <ChatScrollToBottom />
            <ChatComposer
            value={draft}
            onChange={(value) => {
              setDraft(value)
              setSlashSel(0)
              if (value.trim() === '') setHistIdx(null)
            }}
            onSubmit={handleSend}
            ariaLabel="Describe the task, or type / for commands"
            placeholder="Describe the task, or type / for commands"
            sendLabel="Start run"
            stopLabel="Stop"
            busy={live}
            onStop={stopAvailable || pending !== null ? handleStop : undefined}
            attachments={slashList}
            leading={
              <Tooltip content="Choose the local volume file">
                <IconButton
                  variant="ghost"
                  size="sm"
                  aria-label="Choose the local volume file"
                  disabled={picking}
                  aria-busy={picking || undefined}
                  onClick={() => void chooseSource()}
                >
                  <PlusIcon aria-hidden />
                </IconButton>
              </Tooltip>
            }
            endActions={
              <Tooltip content="Settings: model, volume, endpoints, budget">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSettingsOpen(true)}
                >
                  <BotIcon aria-hidden />
                  {settings?.model ?? '…'}
                </Button>
              </Tooltip>
            }
            textareaProps={{ onKeyDown: composerKeyDown }}
            />
          </Chat>
        ) : (
          <BusyRegion busy fill label="Loading workspace" />
        )}
      </FillColumn>
    </div>
  )

  // Always mounted: collapsing the stage only hides it via CSS so the
  // preview iframe + Wasm raycaster stay alive but dormant.
  const stagePane = (
    <div className="gav-stage-pane" data-pane="stage">
      <LiveStage
        source={source}
        previewImport={previewImport}
        onPreviewImport={setPreviewImport}
        onSourceChange={handleStageSourceChange}
      />
    </div>
  )

  const latestImage = transcript
    ? [...transcript.blocks].reverse().find((block) => block.kind === 'image')
    : undefined

  return (
    <>
      <ClippedNavShell
        className="gav-shell"
        navMode={navOpen ? 'drawer' : 'hidden'}
        onNavCrowded={() => setNavOpen(false)}
        topBar={
          <TopAppBar
            title="Agentic Volume"
            leading={
              <Tooltip content={navToggleLabel}>
                <IconButton
                  variant="ghost"
                  aria-label={navToggleLabel}
                  aria-pressed={navOpen}
                  onClick={() => setNavOpen((open) => !open)}
                >
                  {navOpen ? <PanelLeftIcon size={16} aria-hidden /> : <MenuIcon aria-hidden />}
                </IconButton>
              </Tooltip>
            }
            trailing={
              <div className="gav-topbar-trailing">
                {isNarrow ? (
                  <Tabs
                    tabs={[
                      { id: 'chat', label: 'Chat' },
                      { id: 'stage', label: 'Stage' },
                    ]}
                    activeId={narrowView}
                    onChange={setNarrowView}
                    size="sm"
                    ariaLabel="Workbench view"
                  />
                ) : null}
                {!isNarrow ? (
                  <>
                    <Tooltip content="Download .gsc + preview PNG to your downloads">
                      <IconButton
                        variant="ghost"
                        size="sm"
                        aria-label="Download .gsc and preview screenshot"
                        disabled={!stageOpen}
                        onClick={() => window.dispatchEvent(new CustomEvent(STAGE_EXPORT_EVENT))}
                      >
                        <DownloadIcon aria-hidden />
                      </IconButton>
                    </Tooltip>
                    <Tooltip content={stageToggleLabel}>
                      <Button variant="ghost" size="sm" aria-pressed={stageOpen} onClick={() => setStageOpen((v) => !v)}>
                        {stageOpen ? 'Collapse stage' : 'Expand stage'}
                      </Button>
                    </Tooltip>
                  </>
                ) : null}
                <Tooltip content={asideToggleLabel}>
                  <IconButton
                    variant="ghost"
                    aria-label={asideToggleLabel}
                    aria-pressed={asideOpen}
                    onClick={() => setAsideOpen((open) => !open)}
                  >
                    <PanelRightIcon size={16} aria-hidden />
                  </IconButton>
                </Tooltip>
              </div>
            }
          />
        }
        nav={
          navOpen ? (
            <NavigationDrawer variant="standard" ariaLabel="Runs">
              <NavigationDrawerNewChat label="New task" disabled={live} onClick={handleNewTask} />
              {runs.length === 0 ? (
                <EmptyState
                  size="sm"
                  title="No runs yet"
                  description="Send the first task from the composer, or /task-a for the audited preset."
                />
              ) : (
                runs.slice(0, 12).map((run) => (
                  <NavigationDrawerItem
                    key={run.runId}
                    icon={<FileIcon aria-hidden />}
                    label={`${run.runId} — ${run.task.slice(0, 40)}`}
                    active={run.runId === activeRunId}
                    onClick={() => openRun(run.runId)}
                  />
                ))
              )}
            </NavigationDrawer>
          ) : null
        }
      >
        <div className="fynns-destination-app-shell-body">
          <div className="fynns-destination-app-shell-canvas fynns-scroll" ref={canvasRef}>
            {isNarrow ? (
              <div className="gav-workbench gav-workbench--narrow">
                {!stageOpen || narrowView === 'chat' ? chatPane : stagePane}
              </div>
            ) : (
              <SplitPane
                start={chatPane}
                end={stagePane}
                size={stageOpen ? splitChatPx : rowWidth}
                onSizeChange={handleSplitSizeChange}
                minStart={chatMinPx}
                minEnd={stageMinPx}
                disableResize={!stageOpen}
                className={stageOpen ? undefined : 'gav-split--stage-closed'}
                label="Resize Chat and Live Preview (50/50 default)"
              />
            )}
          </div>
          <EndAside open={asideOpen}>
            <PageScroll>
              {transcript ? (
                <div className="fynns-unit-stack">
                  <Card title="Run">
                    <List>
                      <ListItem headline="State" trailingSupportingText={runStateLabel(transcript.status, pending)} />
                      <ListItem
                        headline="Observations"
                        trailingSupportingText={`${transcript.used} / ${transcript.budget}`}
                      />
                      <ListItem headline="Model" supportingText={transcript.model} />
                      <ListItem headline="Volume" supportingText={transcript.volumePath || source} lines={2} />
                      <ListItem headline="Task" supportingText={transcript.task} lines={3} />
                    </List>
                    <LinearProgress
                      value={transcript.budget > 0 ? transcript.used / transcript.budget : 0}
                      label={`Observation budget used: ${transcript.used} of ${transcript.budget}`}
                    />
                  </Card>
                  <Card title="Verification">
                    <BusyRegion busy={verify.running} label="Verifying evidence">
                      {verify.result ? (
                        <div className="fynns-unit-stack">
                          <CodeBlock label="verify summary" code={verify.result.summary.join('\n')} />
                          {verify.result.output ? (
                            <Collapsible title="Verifier output" chrome="plain">
                              <CodeBlock variant="plain" code={verify.result.output} />
                            </Collapsible>
                          ) : null}
                        </div>
                      ) : (
                        <EmptyState
                          size="sm"
                          title="Not verified yet"
                          description="Read-only check: execution / image transport / visual-quality UNVERIFIED."
                        />
                      )}
                    </BusyRegion>
                    <div className="fynns-control-cluster fynns-control-cluster--start-align">
                      <Button
                        variant="primary"
                        size="sm"
                        disabled={verify.running || !activeRunId}
                        onClick={() => void runSlash('verify', '')}
                      >
                        {verify.running ? 'Verifying…' : 'Verify evidence'}
                      </Button>
                    </div>
                  </Card>
                  <Card title="Evidence">
                    <div className="fynns-control-cluster fynns-control-cluster--start-align">
                      <Button variant="ghost" size="sm" disabled={!activeRunId} onClick={() => void runSlash('copy-chat', '')}>
                        Copy chat
                      </Button>
                      <Button variant="ghost" size="sm" disabled={!activeRunId} onClick={() => void runSlash('copy-json', '')}>
                        Copy JSON
                      </Button>
                      <Button variant="ghost" size="sm" disabled={!activeRunId} onClick={() => void runSlash('export-chat', '')}>
                        Download .log
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={!activeRunId}
                        onClick={() => void runSlash('export-images', '')}
                      >
                        Frames .zip
                      </Button>
                      <Button variant="ghost" size="sm" disabled={!activeRunId} onClick={() => void runSlash('open-folder', '')}>
                        Open folder
                      </Button>
                    </div>
                    {latestImage ? (
                      <List>
                        <ListItem
                          headline="Latest frame"
                          supportingText={String((latestImage.data as { image?: unknown })?.image ?? '')}
                          lines={2}
                        />
                      </List>
                    ) : null}
                  </Card>
                  <Card title="Run files">
                    <ExpandableCodeBlock
                      label="transcript blocks (JSON)"
                      code={JSON.stringify(
                        transcript.blocks.map((block) => ({
                          kind: block.kind,
                          title: block.title,
                          subtitle: block.subtitle,
                          status: block.status,
                        })),
                        null,
                        2,
                      )}
                      previewLines={14}
                    />
                  </Card>
                </div>
              ) : (
                <EmptyState
                  size="sm"
                  title="No open run"
                  description="Run state, budget, verification and evidence appear here while a run is open."
                />
              )}
            </PageScroll>
          </EndAside>
        </div>
      </ClippedNavShell>

      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        settings={settings ?? ({} as TuiSettings)}
        volumes={volumes}
        onSave={handleSaveSettings}
        saving={settingsSaving}
      />
      <VolumesDialog
        open={volumesOpen}
        onOpenChange={setVolumesOpen}
        volumes={volumes}
        current={source}
        loading={volumesLoading}
        onPick={handlePickVolume}
      />
      <HistoryDialog
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        runs={runs}
        activeRunId={activeRunId}
        loading={!runsLoaded}
        onOpen={(run) => openRun(run.runId)}
        onArchiveAll={() => setArchiveConfirm(true)}
        archiving={archiving}
      />
      <BatchDialog
        open={batchOpen}
        onOpenChange={setBatchOpen}
        taskPrefill={transcript?.task ?? ''}
        onStart={handleStartBatch}
        starting={batchStarting}
      />
      <BatchDetailDialog
        open={batchDetail !== null}
        onOpenChange={(open) => {
          if (!open) setBatchDetail(null)
        }}
        batch={batchDetail}
        onOpenMember={(runId) => {
          setBatchDetail(null)
          openRun(runId)
        }}
        onCancel={async () => {
          if (!batchDetail) return
          setBatchCancelling(true)
          try {
            await tuiCancelBatch(batchDetail.batchId)
            const detail = await tuiBatchDetail(batchDetail.batchId)
            setBatchDetail(detail)
          } finally {
            setBatchCancelling(false)
          }
        }}
        cancelling={batchCancelling}
      />
      <GlossaryDialog open={glossaryOpen} onOpenChange={setGlossaryOpen} entries={glossary} />
      <HelpDialog open={helpOpen} onOpenChange={setHelpOpen} />

      <ConfirmDialog
        open={archiveConfirm}
        onOpenChange={setArchiveConfirm}
        title="Archive all runs?"
        description="Every history run moves to runtime/archive (live runs are kept). No unarchive: move the folders back manually."
        confirmLabel="Archive all"
        danger
        loading={archiving}
        onConfirm={() => void handleArchiveAll()}
      />
      <ConfirmDialog
        open={quitConfirm}
        onOpenChange={setQuitConfirm}
        title="Stop and clear?"
        description="Stops the live run (evidence stays on disk) and clears the view. The backend keeps serving; the TUI hard-stop semantics for Ollama do not apply in the browser."
        confirmLabel="Stop and clear"
        danger
        onConfirm={() => {
          if (activeRunId) void tuiStopRun(activeRunId).catch(() => undefined)
          setQuitConfirm(false)
          handleNewTask()
        }}
      />

      <Dialog
        open={zoomOpen}
        onOpenChange={setZoomOpen}
        title="Renderer frame"
        description={zoom?.caption}
        size="lg"
        showCloseButton
        closeAriaLabel="Close frame preview"
      >
        {zoom ? <img className="gav-zoom-image" src={zoom.url} alt={zoom.alt} /> : null}
      </Dialog>

      <SnackbarHost />
    </>
  )
}

export default App
